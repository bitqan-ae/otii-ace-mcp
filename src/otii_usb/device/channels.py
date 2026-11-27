# channels.py: the device's channel table (chinfo) and sample decoding
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
# The device reports one 16-byte record per channel: id, flags (sample
# format float32 or int32, whether to apply the scale, channel kind),
# sample rate and scale. ChannelInfo decodes a block of raw samples into a
# float64 numpy array in SI units with np.frombuffer (SW-006, SW-007);
# encode_samples is its inverse for the simulator.

"""Channel capabilities as reported by the ``chinfo`` property, and sample decoding."""
import struct
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
from numpy.typing import NDArray

from ..errors import ProtocolError
from ..protocol.constants import channel_name

_RECORD = struct.Struct("<HHB3xIf")   # channel, flags, aux, sample rate, scale
_FLAG_FLOAT = 0x8000
_FLAG_SCALED = 0x4000
_KIND_MASK = 0x00FF


class ChannelKind(IntEnum):
    ANALOG = 0
    UART = 1
    DIGITAL = 2


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    channel: int
    kind: int
    float_samples: bool
    scaled: bool
    scale: float
    sample_rate: int
    aux: int = 0

    @property
    def name(self) -> str:
        return channel_name(self.channel)

    @property
    def is_analog(self) -> bool:
        return self.kind == ChannelKind.ANALOG

    @classmethod
    def from_record(cls, record: bytes) -> "ChannelInfo":
        channel, flags, aux, rate, scale = _RECORD.unpack(record)
        return cls(channel=channel, kind=flags & _KIND_MASK, float_samples=bool(flags & _FLAG_FLOAT),
                   scaled=bool(flags & _FLAG_SCALED), scale=scale, sample_rate=rate, aux=aux)

    def to_record(self) -> bytes:
        flags = (self.kind & _KIND_MASK) | (_FLAG_FLOAT if self.float_samples else 0) \
            | (_FLAG_SCALED if self.scaled else 0)
        return _RECORD.pack(self.channel, flags, self.aux, self.sample_rate, self.scale)

    @property
    def wire_dtype(self) -> np.dtype:
        return np.dtype("<f4" if self.float_samples else "<i4")

    def decode_samples(self, data: bytes, count: int) -> NDArray[np.float64]:
        """Decode ``count`` raw samples into SI values (float64)."""
        if not self.is_analog:
            raise ValueError(f"channel {self.name} is not an analog channel")
        if len(data) < 4 * count:
            raise ProtocolError(f"channel {self.name}: expected {count} samples")
        values = np.frombuffer(data, dtype=self.wire_dtype, count=count).astype(np.float64)
        if self.scaled:
            values *= self.scale
        return values

    def encode_samples(self, values: NDArray[np.floating]) -> bytes:
        """Inverse of :meth:`decode_samples` (used by the simulator)."""
        raw = np.asarray(values, dtype=np.float64)
        if self.scaled:
            raw = raw / self.scale
        if not self.float_samples:
            raw = np.rint(raw)
        return raw.astype(self.wire_dtype).tobytes()


class ChannelTable(Mapping[int, ChannelInfo]):
    """Immutable mapping ``channel id -> ChannelInfo``."""

    def __init__(self, channels: Mapping[int, ChannelInfo]) -> None:
        self._channels = dict(channels)

    @classmethod
    def parse(cls, raw: bytes) -> "ChannelTable":
        if len(raw) % _RECORD.size:
            raise ProtocolError(f"chinfo length {len(raw)} is not a multiple of {_RECORD.size}")
        infos = (ChannelInfo.from_record(raw[i:i + _RECORD.size])
                 for i in range(0, len(raw), _RECORD.size))
        return cls({info.channel: info for info in infos})

    def encode(self) -> bytes:
        return b"".join(info.to_record() for info in self._channels.values())

    def __getitem__(self, channel: int) -> ChannelInfo:
        return self._channels[channel]

    def __iter__(self) -> Iterator[int]:
        return iter(self._channels)

    def __len__(self) -> int:
        return len(self._channels)
