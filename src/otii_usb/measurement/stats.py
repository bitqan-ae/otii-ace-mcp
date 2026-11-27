# stats.py: statistics over sample arrays
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
# Stats (mean, min, max, standard deviation, count) from an array,
# RunningStats for streams without keeping samples, and MeasurementSummary
# that names the main channels voltage_v, current_a and power_w in its
# dictionary form.

"""Summary statistics over sample arrays."""
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from ..protocol.constants import CHANNEL_UNITS, ChannelId, channel_name

_LABELS: dict[int, str] = {ChannelId.MV: "voltage_v", ChannelId.MC: "current_a",
                           ChannelId.MP: "power_w"}


@dataclass(frozen=True, slots=True)
class Stats:
    mean: float
    minimum: float
    maximum: float
    std: float
    count: int

    @classmethod
    def of(cls, samples: NDArray[np.floating]) -> "Stats":
        values = np.asarray(samples, dtype=np.float64)
        if values.size == 0:
            raise ValueError("no samples")
        return cls(mean=float(values.mean()), minimum=float(values.min()),
                   maximum=float(values.max()), std=float(values.std()), count=int(values.size))

    def to_dict(self) -> dict:
        return {"avg": self.mean, "min": self.minimum, "max": self.maximum, "std": self.std,
                "samples": self.count}


class RunningStats:
    """Incremental mean/min/max/std over chunks of samples (no samples kept)."""

    def __init__(self) -> None:
        self.count = 0
        self._sum = 0.0
        self._sum_sq = 0.0
        self._min = np.inf
        self._max = -np.inf

    def update(self, samples: NDArray[np.floating]) -> None:
        values = np.asarray(samples, dtype=np.float64)
        if values.size == 0:
            return
        self.count += int(values.size)
        self._sum += float(values.sum())
        self._sum_sq += float(np.dot(values, values))
        self._min = min(self._min, float(values.min()))
        self._max = max(self._max, float(values.max()))

    def result(self) -> Stats | None:
        if not self.count:
            return None
        mean = self._sum / self.count
        variance = max(0.0, self._sum_sq / self.count - mean * mean)
        return Stats(mean=mean, minimum=self._min, maximum=self._max,
                     std=float(np.sqrt(variance)), count=self.count)


@dataclass(frozen=True, slots=True)
class MeasurementSummary:
    duration: float
    channels: Mapping[int, Stats] = field(default_factory=dict)
    missing_packets: int = 0

    @property
    def voltage(self) -> Stats | None:
        return self.channels.get(ChannelId.MV)

    @property
    def current(self) -> Stats | None:
        return self.channels.get(ChannelId.MC)

    @property
    def power(self) -> Stats | None:
        return self.channels.get(ChannelId.MP)

    def to_dict(self) -> dict:
        result: dict = {"duration_s": self.duration, "missing_packets": self.missing_packets}
        for channel, stats in self.channels.items():
            result[_LABELS.get(channel, channel_name(channel))] = stats.to_dict()
        return result


def unit_of(channel: int) -> str:
    try:
        return CHANNEL_UNITS.get(ChannelId(channel), "")
    except ValueError:
        return ""
