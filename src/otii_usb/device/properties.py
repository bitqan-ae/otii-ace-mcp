# properties.py: typed device properties - codecs, specifications and
# the client that reads and writes them
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
# A PropertySpec names a property, its wire id (base id plus attribute
# selector), its Codec (int, bool, float32, scaled int, string, bytes) and
# whether this driver may write it. Properties is the catalogue of what the
# driver uses; only entries marked writable can be set (SAF-006).
# PropertyClient turns get/set into GET_PROPERTY / SET_PROPERTY requests.

"""Typed device properties: value codecs, property specifications and a client to access them."""
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..errors import ProtocolError
from ..protocol.constants import Command, PropertyAttr, PropertyId
from ..protocol.session import Session

_I32 = struct.Struct("<i")
_F32 = struct.Struct("<f")
_ID_AND_ZERO = struct.Struct("<II")
_ID = struct.Struct("<I")


class Codec[T](ABC):
    """Converts between a Python value and its on-wire property representation."""

    @abstractmethod
    def decode(self, data: bytes) -> T: ...

    @abstractmethod
    def encode(self, value: T) -> bytes: ...


def _unpack(fmt: struct.Struct, data: bytes) -> tuple:
    if len(data) < fmt.size:
        raise ProtocolError(f"property value too short ({len(data)} < {fmt.size} bytes)")
    return fmt.unpack_from(data)


class Int32Codec(Codec[int]):
    def decode(self, data: bytes) -> int:
        return _unpack(_I32, data)[0]

    def encode(self, value: int) -> bytes:
        return _I32.pack(int(value))


class BoolCodec(Codec[bool]):
    def decode(self, data: bytes) -> bool:
        return _unpack(_I32, data)[0] != 0

    def encode(self, value: bool) -> bytes:
        return _I32.pack(1 if value else 0)


class Float32Codec(Codec[float]):
    def decode(self, data: bytes) -> float:
        return _unpack(_F32, data)[0]

    def encode(self, value: float) -> bytes:
        return _F32.pack(float(value))


class ScaledIntCodec(Codec[float]):
    """Integer on the wire, scaled to SI units (e.g. ``unit=1e-6`` for micro-volts)."""

    def __init__(self, unit: float) -> None:
        self.unit = unit

    def decode(self, data: bytes) -> float:
        return _unpack(_I32, data)[0] * self.unit

    def encode(self, value: float) -> bytes:
        return _I32.pack(round(value / self.unit))


class StringCodec(Codec[str]):
    def decode(self, data: bytes) -> str:
        return data.split(b"\0", 1)[0].decode("utf-8", errors="replace")

    def encode(self, value: str) -> bytes:
        return value.encode()


class BytesCodec(Codec[bytes]):
    def decode(self, data: bytes) -> bytes:
        return data

    def encode(self, value: bytes) -> bytes:
        return bytes(value)


@dataclass(frozen=True, slots=True)
class PropertySpec[T]:
    """Describes one device property: where it lives, how it is encoded, whether it may be set."""
    name: str
    base: PropertyId
    codec: Codec[T]
    attr: PropertyAttr = PropertyAttr.VALUE
    writable: bool = False
    unit: str = ""

    @property
    def wire_id(self) -> int:
        return int(self.base) | int(self.attr)


class Properties:
    """Catalogue of the properties this package knows how to use."""
    NAME = PropertySpec("name", PropertyId.NAME, StringCodec())
    FIRMWARE_VERSION = PropertySpec("fwversion", PropertyId.FIRMWARE_VERSION, StringCodec())
    BOOT_VERSION = PropertySpec("bootversion", PropertyId.BOOT_VERSION, StringCodec())
    HARDWARE_VERSION = PropertySpec("hwversion", PropertyId.HARDWARE_VERSION, StringCodec())
    SERIAL_NUMBER = PropertySpec("uid", PropertyId.UID, StringCodec())
    MAIN_OUTPUT = PropertySpec("main", PropertyId.MAIN_OUTPUT, BoolCodec(), writable=True)
    MAIN_VOLTAGE = PropertySpec("mainvolt", PropertyId.MAIN_VOLTAGE, ScaledIntCodec(1e-6),
                                writable=True, unit="V")
    MAIN_VOLTAGE_MAX = PropertySpec("mainvolt_max", PropertyId.MAIN_VOLTAGE, ScaledIntCodec(1e-6),
                                    attr=PropertyAttr.MAX, unit="V")
    CURRENT_LIMIT = PropertySpec("maxcur", PropertyId.MAX_CURRENT, Float32Codec(),
                                 attr=PropertyAttr.FLOAT, writable=True, unit="A")
    EXPANSION_OUTPUT_VOLTAGE = PropertySpec("expoutvolt", PropertyId.EXPANSION_OUTPUT_VOLTAGE,
                                            ScaledIntCodec(1e-6), writable=True, unit="V")
    CHANNEL_INFO = PropertySpec("chinfo", PropertyId.CHANNEL_INFO, BytesCodec())


class PropertyClient:
    """Reads and writes :class:`PropertySpec` values through a :class:`Session`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get[T](self, spec: PropertySpec[T]) -> T:
        data = self._session.request(Command.GET_PROPERTY, _ID_AND_ZERO.pack(spec.wire_id, 0))
        return spec.codec.decode(data)

    def set[T](self, spec: PropertySpec[T], value: T) -> None:
        if not spec.writable:
            raise PermissionError(f"property {spec.name!r} is read-only in this driver")
        self._session.request(Command.SET_PROPERTY, _ID.pack(spec.wire_id) + spec.codec.encode(value))
