# base.py: the Transport interface every byte pipe implements
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
# write() sends all bytes, read(timeout) returns what arrived (possibly
# nothing), close() is idempotent. SerialTransport talks to the real device,
# SimulatedOtii emulates it; everything above this layer works with either
# (SW-002, SW-011).

"""Abstract byte transport between host and device."""
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self


class Transport(ABC):
    """A bidirectional, unframed byte pipe.

    Implementations only move bytes; framing and semantics live in :mod:`otii_usb.protocol`.
    """

    @property
    def name(self) -> str:
        """Human readable identifier (e.g. the device path)."""
        return type(self).__name__

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write all of ``data`` or raise :class:`~otii_usb.errors.TransportError`."""

    @abstractmethod
    def read(self, timeout: float) -> bytes:
        """Return available bytes, waiting at most ``timeout`` seconds; ``b""`` if none arrived."""

    @abstractmethod
    def close(self) -> None:
        """Release the transport. Must be idempotent."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()
