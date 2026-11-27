# framing.py: frames of u32 magic, u16 length, u16 checksum, payload
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
# The same framing is used in both directions. FrameDecoder takes arbitrary
# chunks from the transport and returns complete payloads; bytes outside
# frames (text-mode output after reset) and frames with a bad checksum are
# skipped, and the decoder resynchronises on the next magic word
# (REQ-PROT-LINK-001).

"""Frame layer: ``u32 magic | u16 length | u16 checksum | payload`` (little endian, both ways)."""
import struct
from collections.abc import Callable

from ..errors import ProtocolError
from .constants import FRAME_MAGIC

HEADER = struct.Struct("<IHH")
MAX_PAYLOAD = 0xFFFF
_MAGIC_BYTES = struct.pack("<I", FRAME_MAGIC)


def checksum(payload: bytes) -> int:
    """16-bit additive checksum over the payload bytes."""
    return sum(payload) & 0xFFFF


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"payload too large ({len(payload)} bytes)")
    return HEADER.pack(FRAME_MAGIC, len(payload), checksum(payload)) + payload


class FrameDecoder:
    """Incremental decoder: feed arbitrary chunks, get complete payloads back.

    Bytes outside frames (e.g. text-mode output) and frames with a bad checksum are skipped;
    the decoder resynchronises on the next magic word.
    """

    def __init__(self, on_discard: Callable[[bytes], None] | None = None) -> None:
        self._buffer = bytearray()
        self._on_discard = on_discard
        self.discarded_bytes = 0
        self.checksum_errors = 0

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer += data
        payloads: list[bytes] = []
        while True:
            start = self._buffer.find(_MAGIC_BYTES)
            if start < 0:
                # keep a possible partial magic word at the end
                self._discard(max(0, len(self._buffer) - (len(_MAGIC_BYTES) - 1)))
                return payloads
            self._discard(start)
            if len(self._buffer) < HEADER.size:
                return payloads
            _, length, expected = HEADER.unpack_from(self._buffer)
            end = HEADER.size + length
            if len(self._buffer) < end:
                return payloads
            payload = bytes(self._buffer[HEADER.size:end])
            if checksum(payload) != expected:
                self.checksum_errors += 1
                self._discard(1)
                continue
            del self._buffer[:end]
            payloads.append(payload)

    def _discard(self, count: int) -> None:
        if count <= 0:
            return
        if self._on_discard is not None:
            self._on_discard(bytes(self._buffer[:count]))
        self.discarded_bytes += count
        del self._buffer[:count]
