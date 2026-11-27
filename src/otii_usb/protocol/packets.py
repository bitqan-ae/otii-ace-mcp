# packets.py: typed packets inside frames, with parsers and encoders
# for both directions
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
# Host to device: Request (transaction id, command, arguments). Device to
# host: Response (transaction id, error code, data), DataPacket (one per
# millisecond: sequence number and per-channel sample blocks), PropertyUpdate
# and Alert. The encoders for the device direction are used by the
# simulator, so tests exercise exactly the parsers used with hardware.

"""Typed packets carried inside frames, with encoders for both directions."""
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from ..errors import ProtocolError
from .constants import PacketType

SAMPLE_SIZE = 4
_U32 = struct.Struct("<I")
_REQUEST = struct.Struct("<II")       # txn, command
_RESPONSE = struct.Struct("<III")     # type, txn, error code
_DATA = struct.Struct("<II")          # type, sequence (one packet per millisecond)
_BLOCK = struct.Struct("<HHI")        # channel, sample count, sample rate


@dataclass(frozen=True, slots=True)
class Request:
    txn: int
    command: int
    args: bytes = b""


@dataclass(frozen=True, slots=True)
class Response:
    txn: int
    error_code: int
    data: bytes

    @property
    def ok(self) -> bool:
        return self.error_code == 0


@dataclass(frozen=True, slots=True)
class PropertyUpdate:
    data: bytes


@dataclass(frozen=True, slots=True)
class Alert:
    data: bytes


@dataclass(frozen=True, slots=True)
class SampleBlock:
    channel: int
    count: int
    sample_rate: int
    data: bytes


@dataclass(frozen=True, slots=True)
class DataPacket:
    sequence: int
    blocks: tuple[SampleBlock, ...]


@dataclass(frozen=True, slots=True)
class UnknownPacket:
    packet_type: int
    data: bytes


type Packet = Response | PropertyUpdate | Alert | DataPacket | UnknownPacket


# ---------------------------------------------------------------- host -> device

def encode_request(request: Request) -> bytes:
    return _REQUEST.pack(request.txn, request.command) + request.args


def parse_request(payload: bytes) -> Request:
    if len(payload) < _REQUEST.size:
        raise ProtocolError(f"request too short ({len(payload)} bytes)")
    txn, command = _REQUEST.unpack_from(payload)
    return Request(txn, command, payload[_REQUEST.size:])


# ---------------------------------------------------------------- device -> host

def encode_response(response: Response) -> bytes:
    return _RESPONSE.pack(PacketType.RESPONSE, response.txn, response.error_code) + response.data


def encode_data_packet(packet: DataPacket) -> bytes:
    parts = [_DATA.pack(PacketType.DATA, packet.sequence)]
    for block in packet.blocks:
        if len(block.data) != block.count * SAMPLE_SIZE:
            raise ProtocolError("sample block size does not match its count")
        parts.append(_BLOCK.pack(block.channel, block.count, block.sample_rate) + block.data)
    return b"".join(parts)


def _parse_blocks(payload: bytes, offset: int) -> Iterable[SampleBlock]:
    while offset + _BLOCK.size <= len(payload):
        channel, count, rate = _BLOCK.unpack_from(payload, offset)
        offset += _BLOCK.size
        end = offset + count * SAMPLE_SIZE
        if end > len(payload):
            raise ProtocolError(f"truncated sample block for channel {channel}")
        yield SampleBlock(channel, count, rate, payload[offset:end])
        offset = end
    if offset != len(payload):
        raise ProtocolError("trailing bytes after sample blocks")


def parse_packet(payload: bytes) -> Packet:
    if len(payload) < _U32.size:
        raise ProtocolError(f"packet too short ({len(payload)} bytes)")
    (packet_type,) = _U32.unpack_from(payload)
    match packet_type:
        case PacketType.RESPONSE:
            if len(payload) < _RESPONSE.size:
                raise ProtocolError("truncated response")
            _, txn, error = _RESPONSE.unpack_from(payload)
            return Response(txn, error, payload[_RESPONSE.size:])
        case PacketType.DATA:
            if len(payload) < _DATA.size:
                raise ProtocolError("truncated data packet")
            _, sequence = _DATA.unpack_from(payload)
            return DataPacket(sequence, tuple(_parse_blocks(payload, _DATA.size)))
        case PacketType.PROPERTY_UPDATE:
            return PropertyUpdate(payload[_U32.size:])
        case PacketType.ALERT:
            return Alert(payload[_U32.size:])
        case _:
            return UnknownPacket(packet_type, payload[_U32.size:])
