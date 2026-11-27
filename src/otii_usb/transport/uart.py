# uart.py: receive-only serial terminal with host arrival times
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
# UartReader listens on the serial console of the device under test while a
# recording runs (REQ-MEAS-REC-009). It opens the port read-only, so nothing
# is ever sent to the device under test, takes an exclusive flock like
# SerialTransport (so the Otii's own port is refused while it is in use),
# switches the line to raw 8N1 at the given baud rate and discards what was
# buffered before. A thread waits in select() and stamps every chunk with
# time.monotonic() right after the read; stop() ends it within 50 ms.
#
# A USB serial adapter powered by the device under test only appears once
# that is powered, and disappears when it is switched off. A port that
# exists at the start is opened at once, so a busy port or one that is not
# a serial port fails before the recording. Otherwise, and after every
# disconnect, the thread polls every 5 ms and opens the port as soon as it
# can (udev may still be setting its permissions when the node appears).
# The times the port was open are kept, so a recording can show when text
# could not have been received. The port's previous settings are restored
# when it is closed. Standard library only (DC-ARCH-PKG-002).

"""Receive-only serial port that timestamps what arrives."""
import errno
import fcntl
import os
import select
import termios
import threading
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from ..errors import PortBusyError, TransportError

DEFAULT_BAUD = 115200
_POLL = 0.05                        # select() timeout while the port is open [s]
_WAIT = 0.005                       # polling for the port to appear [s]


def baud_constant(baud: int) -> int:
    """The termios speed constant for ``baud``; ValueError for a rate termios does not know."""
    speed = getattr(termios, f"B{baud}", None) if baud > 0 else None
    if speed is None:
        raise ValueError(f"unsupported baud rate {baud}")
    return speed


@dataclass(frozen=True, slots=True)
class Chunk:
    """Bytes returned by one read and the host's time.monotonic() right after it."""
    time: float
    data: bytes
    connection: int = 0
    """Index into :attr:`Capture.open`: the opening of the port this was read through."""


@dataclass(frozen=True, slots=True)
class Capture:
    """What a :class:`UartReader` received, with the host times the port was open."""
    chunks: tuple[Chunk, ...]
    open: tuple[tuple[float, float | None], ...]
    """(opened, closed) per connection; closed is None if it was open until stop()."""
    error: str | None = None
    """Why the port could not be read, if it never opened or failed for good."""


class _Gone(Exception):
    """The port is not there (yet), or disappeared."""


class UartReader:
    """Reads a serial port in a background thread and keeps every chunk with its time.

    The port may appear only later (an adapter powered by the device under test); the
    thread waits for it, and for it to come back after a disconnect.
    """

    def __init__(self, path: str, baud: int = DEFAULT_BAUD) -> None:
        self._speed = baud_constant(baud)
        self.path = path
        self.baud = baud
        self._fd: int | None = None
        self._saved: list | None = None          # the port's settings before ours
        self._chunks: list[Chunk] = []
        self._open: list[tuple[float, float | None]] = []
        self._error: str | None = None          # the reader gave up
        self._attempt: str | None = None        # why the last opening failed
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if os.path.exists(path):
            try:
                self._connect()
            except _Gone as e:
                if isinstance(e.__cause__, PermissionError):
                    raise TransportError(self._attempt or path) from None
                # otherwise e.g. unplugged right now: the thread waits for it

    def _connect(self) -> None:
        """Open, lock and configure the port; _Gone while it is not (fully) there."""
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        except FileNotFoundError:
            raise _Gone from None
        except PermissionError as e:              # also while udev sets the permissions
            self._attempt = f"cannot open {self.path}: {e.strerror}"
            raise _Gone from e
        except OSError as e:
            if e.errno in (errno.ENXIO, errno.ENODEV, errno.EIO):
                self._attempt = f"cannot open {self.path}: {e.strerror}"
                raise _Gone from e
            raise TransportError(f"cannot open {self.path}: {e.strerror}") from e
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PortBusyError(f"{self.path} is in use by another process") from None
            self._configure(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        self._attempt = None
        self._open.append((time.monotonic(), None))

    def _configure(self, fd: int) -> None:
        try:
            attrs = termios.tcgetattr(fd)
            self._saved = [list(a) if isinstance(a, list) else a for a in attrs]
            attrs[0] = 0                                                # iflag
            attrs[1] = 0                                                # oflag
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL     # cflag
            attrs[3] = 0                                                # lflag
            attrs[4] = attrs[5] = self._speed                           # ispeed / ospeed
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIFLUSH)
        except termios.error as e:
            if os.path.exists(self.path) and e.args and e.args[0] == errno.ENOTTY:
                raise TransportError(f"{self.path} is not a serial port") from None
            self._attempt = f"cannot configure {self.path}: {e}"
            raise _Gone from None

    def _disconnect(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            if self._saved is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, self._saved)
                except termios.error:
                    pass                            # gone with the adapter
            os.close(fd)
            opened, _ = self._open[-1]
            self._open[-1] = (opened, time.monotonic())

    @property
    def char_time(self) -> float:
        """Time one 8N1 character takes on the line [s]."""
        return 10.0 / self.baud

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="uart-reader", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._fd is None:
                try:
                    self._connect()
                except _Gone:
                    self._stop.wait(_WAIT)
                    continue
                except TransportError as e:
                    self._error = str(e)
                    return
            try:
                self._read(self._fd)
            except _Gone:
                self._disconnect()

    def _read(self, fd: int) -> None:
        try:
            readable, _, _ = select.select([fd], [], [], _POLL)
            if not readable:
                return
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except (OSError, ValueError) as e:
            raise _Gone from e
        stamp = time.monotonic()
        if not data:
            raise _Gone
        self._chunks.append(Chunk(stamp, data, len(self._open) - 1))

    def stop(self) -> Capture:
        """End the reader thread and return what was received, oldest first."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._thread is None or not self._thread.is_alive():
            self._disconnect()
        error = self._error
        if error is None and not self._open:
            error = self._attempt or f"{self.path} did not appear"
        return Capture(tuple(self._chunks), tuple(self._open), error)

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()


__all__ = ["DEFAULT_BAUD", "Capture", "Chunk", "UartReader", "baud_constant"]
