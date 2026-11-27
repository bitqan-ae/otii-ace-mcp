# serial_text.py: serial terminal lines on the recording's time axis
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
# Turns the timestamped chunks of a UartReader into lines on the recording's
# time axis (REQ-MEAS-REC-009).
#
# HostClock is a SampleSink that notes the host's time.monotonic() when each
# data packet arrives. A packet cannot arrive before it ends on the device's
# time axis, so the smallest difference between arrival and packet end is
# the transport latency at its best; host time minus that offset is the
# recording's time.
#
# split_lines times each line at its first printable byte. A chunk holds
# what arrived since the previous read; its last byte arrived at the read,
# each earlier one a character time (10 bits at the baud rate) before, but
# not before the previous read or the port's opening. A line starts with the
# first printable byte after the port opened (at the start of the recording,
# or when an adapter powered by the device under test appears) and after
# every newline; text continuing a line, however late, gets no marker of its
# own. Escape sequences and control characters
# are removed; after a carriage return the last text written wins; blank
# lines are dropped. SerialText also keeps when the port was open and the
# arrival of every printable character (dated back the same way, numpy per
# chunk), on the recording's time axis; character_rate bins them into
# characters per second.

"""Serial terminal text timed on the recording's time axis."""
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from ..transport.uart import Capture, Chunk
from .binning import PACKET_PERIOD
from .sinks import SampleSink

_ESCAPE = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)?|[@-Z\\-_])")


class HostClock(SampleSink):
    """Maps host time.monotonic() onto the recording's time axis from packet arrivals."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._first: int | None = None
        self.offset = math.inf
        """Host time at the recording's time zero [s], ``inf`` until a packet arrived."""

    def write(self, sequence: int, samples: Mapping[int, NDArray[np.float64]]) -> None:
        now = self._clock()
        if self._first is None:
            self._first = sequence
        end = (sequence - self._first + 1) * PACKET_PERIOD
        self.offset = min(self.offset, now - end)

    def to_recording(self, host_time: float) -> float:
        return host_time - self.offset


@dataclass(frozen=True, slots=True)
class SerialLine:
    time: float
    """Arrival of the line's first printable byte, on the recording's time axis [s]."""
    text: str


@dataclass(frozen=True, slots=True)
class SerialText:
    """What the serial terminal received during a recording."""
    port: str
    baud: int
    bytes: int
    lines: tuple[SerialLine, ...]
    open: tuple[tuple[float, float], ...] = ()
    """When the port was open, on the recording's time axis [s], clipped to the recording."""
    error: str | None = None
    """Why the port could not be read, if it never opened or failed for good."""
    characters: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    """Arrival of every printable character on the recording's time axis [s]."""

    @classmethod
    def of(cls, capture: Capture, char_time: float, to_recording: Callable[[float], float],
           duration: float, *, port: str, baud: int) -> "SerialText":
        opened_at = [opened for opened, _ in capture.open]
        lines = split_lines(capture.chunks, char_time, to_recording, opened_at)
        characters = character_times(capture.chunks, char_time, to_recording, opened_at)
        spans = []
        for opened, closed in capture.open:
            start = max(0.0, to_recording(opened))
            end = duration if closed is None else min(duration, to_recording(closed))
            if end > start:
                spans.append((start, end))
        return cls(port, baud, sum(len(c.data) for c in capture.chunks), tuple(lines),
                   tuple(spans), capture.error, characters)

    def to_dict(self) -> dict:
        result: dict = {"port": self.port, "baud": self.baud, "bytes": self.bytes,
                        "lines": len(self.lines),
                        "open_s": [[round(a, 6), round(b, 6)] for a, b in self.open]}
        return result if self.error is None else result | {"error": self.error}


def clean(raw: bytes) -> str:
    """Printable text of one line: escape sequences, control characters and overwrites gone."""
    text = _ESCAPE.sub("", raw.decode("utf-8", errors="replace")).replace("\t", "    ")
    segments = [s for s in text.split("\r") if s.strip()]
    text = segments[-1] if segments else ""
    return "".join(ch for ch in text if ch.isprintable()).rstrip()


def _printable(byte: int) -> bool:
    return byte >= 0x20 and byte != 0x7f


def _arrival(chunk: Chunk, index, previous_read: float, char_time: float):
    """When byte(s) ``index`` of ``chunk`` arrived: one character time each before the read."""
    return np.maximum(previous_read, chunk.time - (len(chunk.data) - 1 - index) * char_time)


def character_times(chunks: Sequence[Chunk], char_time: float,
                    to_recording: Callable[[float], float],
                    opened: Sequence[float]) -> NDArray[np.float64]:
    """Arrival of every printable character (UTF-8 continuation bytes not counted), sorted."""
    parts = []
    connection: int | None = None
    previous_read = -math.inf
    for chunk in chunks:
        if chunk.connection != connection:
            connection = chunk.connection
            previous_read = opened[connection]
        data = np.frombuffer(chunk.data, dtype=np.uint8)
        keep = (data >= 0x20) & (data != 0x7f) & ~((data >= 0x80) & (data < 0xc0))
        parts.append(_arrival(chunk, np.flatnonzero(keep), previous_read, char_time))
        previous_read = chunk.time
    times = np.concatenate(parts) if parts else np.empty(0)
    return np.asarray(to_recording(times), dtype=np.float64)        # type: ignore[arg-type]


def character_rate(times: NDArray[np.float64], start: float, end: float,
                   bins: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Characters per second in ``bins`` equal bins from ``start`` to ``end``: (edges, rate)."""
    counts, edges = np.histogram(times, bins=bins, range=(start, end))
    return edges, counts / ((end - start) / bins)


def split_lines(chunks: Sequence[Chunk], char_time: float, to_recording: Callable[[float], float],
                opened: Sequence[float]) -> list[SerialLine]:
    """Lines with the arrival time of their first printable byte, oldest first.

    ``opened``: host time each connection (``Chunk.connection``) was opened.
    """
    lines: list[SerialLine] = []
    current = bytearray()
    first: float | None = None
    at_line = True
    connection: int | None = None
    previous_read = -math.inf

    def finish() -> None:
        nonlocal first
        if at_line and first is not None and (text := clean(bytes(current))):
            lines.append(SerialLine(to_recording(first), text))
        current.clear()
        first = None

    for chunk in chunks:
        data, last = chunk.data, len(chunk.data) - 1
        if chunk.connection != connection:          # the port (re)opened: a new stream
            finish()
            connection = chunk.connection
            previous_read = opened[connection]
            at_line = True
        start = 0
        while start <= last:
            newline = data.find(b"\n", start)
            end = last + 1 if newline < 0 else newline
            if first is None:
                for k in range(start, end):
                    if _printable(data[k]):
                        first = float(_arrival(chunk, k, previous_read, char_time))
                        break
            current += data[start:end]
            if newline < 0:
                break
            finish()
            at_line = True
            start = newline + 1
        previous_read = chunk.time
    finish()
    return lines


__all__ = ["HostClock", "SerialLine", "SerialText", "character_rate", "character_times", "clean",
           "split_lines"]
