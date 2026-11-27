# recording.py: an in-memory recording as numpy arrays
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
# Samples per channel in SI units with their sample rates and the number
# of missing packets; timestamps() gives the time axis and summarize()
# the statistics.

"""Recorded sample data."""
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from .stats import MeasurementSummary, Stats


@dataclass(frozen=True)
class Recording:
    """Samples per channel (SI units, float64) captured during one recording."""
    duration: float
    samples: Mapping[int, NDArray[np.float64]]
    sample_rates: Mapping[int, int] = field(default_factory=dict)
    missing_packets: int = 0

    def __getitem__(self, channel: int) -> NDArray[np.float64]:
        return self.samples[channel]

    def timestamps(self, channel: int) -> NDArray[np.float64]:
        """Sample times in seconds relative to the first sample of ``channel``."""
        rate = self.sample_rates.get(channel)
        if not rate:
            raise KeyError(f"no sample rate known for channel {channel}")
        return np.arange(self.samples[channel].size, dtype=np.float64) / rate

    def summarize(self) -> MeasurementSummary:
        stats = {ch: Stats.of(values) for ch, values in self.samples.items() if values.size}
        return MeasurementSummary(self.duration, stats, self.missing_packets)
