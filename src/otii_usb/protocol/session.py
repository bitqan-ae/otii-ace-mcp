# session.py: request/response, streaming and keepalive over a Transport
#
# Copyright (c) 2026 Milosch Meriac <meriac@bitqan.ae>
#    developed at Bitqan Systems Design (bitqan.ae)
#         From Idea to Manufacturable Product
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0
#
# request() sends a command with a fresh transaction id and waits for the
# response with that id; everything else that arrives meanwhile (sample data,
# alerts) goes to listeners. pump() processes packets for a given time, sends
# a keepalive ping every 0.5 s and reports progress; pump_until() does the
# same until a condition holds or a timeout passes. All blocking waits poll a
# cancel event every 50 ms, so another thread can abort a long operation
# quickly when the program shuts down (REQ-SAFE-OUT-004). handshake()
# establishes binary mode, falling back to a text-mode wake-up line between
# attempts. Every wait has a time limit (REQ-PROT-LINK-002).

"""Request/response session over a :class:`~otii_usb.transport.Transport`."""
import itertools
import logging
import struct
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from ..errors import (CommandError, OperationCancelledError, ProtocolError,
                      ResponseTimeoutError)
from ..transport.base import Transport
from .constants import KEEPALIVE_ARGS, TEXT_INIT, Command
from .framing import FrameDecoder, encode_frame
from .packets import Alert, Packet, Request, Response, encode_request, parse_packet

log = logging.getLogger(__name__)

type PacketListener = Callable[[Packet], None]
type ProgressCallback = Callable[[float, float], None]
"""Called as ``progress(elapsed_s, total_s)`` while a timed operation runs."""


@dataclass(frozen=True, slots=True)
class HelloInfo:
    """Hello reply: ``[u32 protocol][u32 max payload][u32 device type][u32 feature bits]``.

    The device type is a four-character code stored as a little-endian u32 (``"Ace "``).
    """
    protocol_version: int
    max_payload: int
    extra: bytes = b""

    @property
    def device_type(self) -> str:
        return self.extra[:4][::-1].decode("ascii", errors="replace").strip() if len(self.extra) >= 4 else ""

    @property
    def feature_bits(self) -> int:
        return struct.unpack_from("<I", self.extra, 4)[0] if len(self.extra) >= 8 else 0

    @classmethod
    def parse(cls, data: bytes) -> "HelloInfo":
        if len(data) < 8:
            raise ProtocolError(f"hello reply too short ({len(data)} bytes)")
        version, max_payload = struct.unpack_from("<II", data)
        return cls(version, max_payload, data[8:])


def _command_name(command: int) -> str:
    try:
        return Command(command).name
    except ValueError:
        return f"0x{command:02x}"


class Session:
    """Owns the framing state of one connection.

    * :meth:`request` sends a command and blocks until its response arrives.
    * Packets that are not the awaited response (sample data, alerts, property updates) are
      dispatched to listeners registered with :meth:`listening`.
    * :meth:`pump` processes incoming packets for a while and keeps the link alive.
    * If ``cancel`` is set (from any thread), blocking calls raise
      :class:`~otii_usb.errors.OperationCancelledError` within ``POLL_INTERVAL`` seconds.
    """

    POLL_INTERVAL = 0.05

    def __init__(self, transport: Transport, *, timeout: float = 2.0,
                 keepalive_interval: float = 0.5, cancel: threading.Event | None = None) -> None:
        self._transport = transport
        self._cancel = cancel
        self._timeout = timeout
        self._keepalive_interval = keepalive_interval
        self._decoder = FrameDecoder(on_discard=self._log_discard)
        self._txn = itertools.count(1)
        self._inbox: deque[Packet] = deque()
        self._fire_and_forget: set[int] = set()
        self._listeners: list[PacketListener] = []
        self._last_keepalive = time.monotonic()
        self.alerts: list[Alert] = []
        """Alert packets received during this session, oldest first."""

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def timeout(self) -> float:
        """Default time limit for a response [s]."""
        return self._timeout

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()

    def handshake(self, *, attempts: int = 6, timeout: float = 0.6) -> HelloInfo:
        """Establish binary mode. Falls back to a text-mode wake-up between attempts."""
        for attempt in range(1, attempts + 1):
            try:
                hello = HelloInfo.parse(self.request(Command.HELLO, timeout=timeout))
            except ResponseTimeoutError:
                log.debug("no hello reply (attempt %d/%d), sending text init", attempt, attempts)
                self._transport.write(TEXT_INIT)
                self.pump(0.2)
                continue
            log.debug("connected: protocol %d, max payload %d", hello.protocol_version,
                      hello.max_payload)
            return hello
        raise ProtocolError("device does not answer the binary hello")

    # ------------------------------------------------------------ requests

    def send(self, command: int, args: bytes = b"") -> int:
        """Send a request without waiting; returns its transaction id."""
        txn = next(self._txn) & 0xFFFFFFFF
        frame = encode_frame(encode_request(Request(txn, command, args)))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("TX %s txn=%d %s", _command_name(command), txn, frame.hex(" "))
        self._transport.write(frame)
        return txn

    def request(self, command: int, args: bytes = b"", *, timeout: float | None = None) -> bytes:
        """Send a request and return the response data; raises on error code or timeout."""
        txn = self.send(command, args)
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)
        while (packet := self._next_packet(deadline)) is not None:
            if isinstance(packet, Response) and packet.txn == txn:
                if not packet.ok:
                    raise CommandError(command, packet.error_code)
                return packet.data
            self._dispatch(packet)
        raise ResponseTimeoutError(f"no reply to {_command_name(command)} (txn {txn})")

    # ------------------------------------------------------------ streaming

    @contextmanager
    def listening(self, listener: PacketListener) -> Iterator[None]:
        self._listeners.append(listener)
        try:
            yield
        finally:
            self._listeners.remove(listener)

    PROGRESS_INTERVAL = 0.1

    def pump(self, duration: float, progress: ProgressCallback | None = None) -> None:
        """Dispatch incoming packets to listeners for ``duration`` seconds."""
        start = time.monotonic()
        deadline = start + duration
        next_report = start
        while (now := time.monotonic()) < deadline:
            if progress is not None and now >= next_report:
                progress(now - start, duration)
                next_report = now + self.PROGRESS_INTERVAL
            self._keepalive_if_due(now)
            packet = self._next_packet(min(deadline, now + self._keepalive_interval,
                                           next_report if progress else deadline))
            if packet is not None:
                self._dispatch(packet)
        if progress is not None:
            progress(duration, duration)

    def pump_until(self, condition: Callable[[], bool], timeout: float) -> bool:
        """Dispatch packets until ``condition()`` holds; False if ``timeout`` s pass first."""
        deadline = time.monotonic() + timeout
        while not condition():
            if (now := time.monotonic()) >= deadline:
                return False
            self._keepalive_if_due(now)
            packet = self._next_packet(min(deadline, now + self._keepalive_interval))
            if packet is not None:
                self._dispatch(packet)
        return True

    def _keepalive_if_due(self, now: float) -> None:
        if now - self._last_keepalive >= self._keepalive_interval:
            self._last_keepalive = now
            self._fire_and_forget.add(self.send(Command.PING, KEEPALIVE_ARGS))

    # ------------------------------------------------------------ receive path

    def _next_packet(self, deadline: float) -> Packet | None:
        while not self._inbox:
            self._raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._fill(min(remaining, self.POLL_INTERVAL))
        return self._inbox.popleft()

    def _raise_if_cancelled(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise OperationCancelledError("operation cancelled")

    def _fill(self, timeout: float) -> None:
        for payload in self._decoder.feed(self._transport.read(timeout)):
            if log.isEnabledFor(logging.DEBUG):
                log.debug("RX %s", payload.hex(" "))
            try:
                self._inbox.append(parse_packet(payload))
            except ProtocolError as e:
                log.warning("dropping malformed packet: %s", e)

    def _dispatch(self, packet: Packet) -> None:
        if isinstance(packet, Response):
            if packet.txn in self._fire_and_forget:
                self._fire_and_forget.discard(packet.txn)
                if not packet.ok:
                    log.warning("keepalive failed with error code %d", packet.error_code)
            else:
                log.debug("ignoring unsolicited response txn=%d", packet.txn)
            return
        if isinstance(packet, Alert):
            log.warning("device alert: %s", packet.data.hex(" "))
            self.alerts.append(packet)
        for listener in tuple(self._listeners):
            listener(packet)

    @staticmethod
    def _log_discard(data: bytes) -> None:
        log.debug("discarding %d non-frame bytes: %r", len(data), data[:64])
