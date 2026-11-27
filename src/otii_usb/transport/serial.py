# serial.py: the Otii's USB CDC-ACM port as a raw, exclusively
# locked byte pipe
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
# Opens /dev/ttyACM* non-blocking, takes an exclusive flock so no second
# process can talk to the device at the same time (SAF-009), and switches
# the line to raw 8N1 (the baud rate is irrelevant on USB CDC-ACM). Reads
# and writes go through select() with timeouts; an empty read on a
# readable port means the device was unplugged. Standard library only.

"""POSIX serial (CDC-ACM) transport using only the standard library."""
import fcntl
import os
import select
import termios

from ..errors import PortBusyError, TransportError
from .base import Transport


class SerialTransport(Transport):
    """Raw, exclusively locked serial port.

    The port is put into raw 8N1 mode; the baud rate is irrelevant for USB CDC-ACM devices.
    """

    def __init__(self, path: str, *, write_timeout: float = 2.0) -> None:
        self._path = path
        self._write_timeout = write_timeout
        try:
            self._fd: int | None = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise TransportError(f"cannot open {path}: {e.strerror}") from e
        try:
            self._lock()
            self._configure()
        except BaseException:
            self.close()
            raise

    @property
    def name(self) -> str:
        return self._path

    def _lock(self) -> None:
        try:
            fcntl.flock(self._require_fd(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PortBusyError(f"{self._path} is in use by another process") from None

    def _configure(self) -> None:
        fd = self._require_fd()
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0                                                   # iflag
        attrs[1] = 0                                                   # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL        # cflag
        attrs[3] = 0                                                   # lflag
        attrs[4] = attrs[5] = termios.B115200                          # ispeed / ospeed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)

    def _require_fd(self) -> int:
        if self._fd is None:
            raise TransportError(f"{self._path} is closed")
        return self._fd

    def write(self, data: bytes) -> None:
        fd = self._require_fd()
        view = memoryview(data)
        while view:
            _, writable, _ = select.select([], [fd], [], self._write_timeout)
            if not writable:
                raise TransportError(f"write to {self._path} timed out")
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                continue
            except OSError as e:
                raise TransportError(f"write to {self._path} failed: {e.strerror}") from e
            view = view[written:]

    def read(self, timeout: float) -> bytes:
        fd = self._require_fd()
        readable, _, _ = select.select([fd], [], [], max(0.0, timeout))
        if not readable:
            return b""
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return b""
        except OSError as e:
            raise TransportError(f"read from {self._path} failed: {e.strerror}") from e
        if not data:
            raise TransportError(f"{self._path} disconnected")
        return data

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)
