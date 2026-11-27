# binning.py: time bins of mean, min and max over multi-channel samples
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
# Groups consecutive samples into bins of a fixed number of samples, also
# across packet boundaries, and returns the bin start time with mean,
# minimum and maximum per column. Time comes from the packet sequence
# numbers (one per millisecond), so a dropped packet leaves a gap instead
# of shifting later samples; a bin never spans a gap. Used for CSV rows and
# for the plot's mean line and min/max band.

"""Time binning of multi-channel sample blocks (mean / min / max per bin)."""
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

PACKET_PERIOD = 1e-3
"""The device sends one data packet per millisecond; its sequence number is the time base."""


@dataclass(frozen=True, slots=True)
class Bins:
    times: NDArray[np.float64]        # (n,)  start of each bin [s]
    mean: NDArray[np.float64]         # (n, columns)
    minimum: NDArray[np.float64]      # (n, columns)
    maximum: NDArray[np.float64]      # (n, columns)

    def __len__(self) -> int:
        return int(self.times.size)

    @classmethod
    def empty(cls, columns: int) -> "Bins":
        z = np.empty((0, columns))
        return cls(np.empty(0), z, z, z)

    @classmethod
    def concat(cls, parts: list["Bins"], columns: int) -> "Bins":
        if not parts:
            return cls.empty(columns)
        return cls(*(np.concatenate([getattr(p, f) for p in parts])
                     for f in ("times", "mean", "minimum", "maximum")))


class Binner:
    """Groups consecutive samples into bins of ``decimation`` samples.

    Bins may span packets. Time comes from packet sequence numbers, so a dropped packet
    shows up as a gap; a bin never spans a gap (the partial bin is emitted first).
    """

    def __init__(self, sample_rate: int, decimation: int, columns: int) -> None:
        if sample_rate <= 0 or decimation <= 0:
            raise ValueError("sample rate and decimation must be positive")
        self.sample_rate = sample_rate
        self.decimation = decimation
        self._columns = columns
        self._carry = np.empty((0, columns))
        self._carry_t0 = 0.0
        self._first_sequence: int | None = None
        self._next_sequence: int | None = None

    @property
    def bin_rate(self) -> float:
        return self.sample_rate / self.decimation

    def feed(self, sequence: int, block: NDArray[np.float64]) -> Bins:
        """Add one packet's samples, shape ``(n, columns)``; return the completed bins."""
        if self._first_sequence is None:
            self._first_sequence = sequence
        t0 = (sequence - self._first_sequence) * PACKET_PERIOD
        parts = []
        if self._next_sequence is not None and sequence != self._next_sequence:
            parts.append(self.flush())
        self._next_sequence = sequence + 1
        if not self._carry.size:
            self._carry_t0 = t0
        data = np.concatenate([self._carry, block]) if self._carry.size else block
        k = self.decimation
        usable = data.shape[0] // k * k
        if usable:
            groups = data[:usable].reshape(-1, k, self._columns)
            times = self._carry_t0 + np.arange(groups.shape[0]) * (k / self.sample_rate)
            parts.append(Bins(times, groups.mean(axis=1), groups.min(axis=1), groups.max(axis=1)))
        self._carry = data[usable:]
        self._carry_t0 += usable / self.sample_rate
        return Bins.concat([p for p in parts if len(p)], self._columns)

    def flush(self) -> Bins:
        """Emit the incomplete bin, if any."""
        if not self._carry.size:
            return Bins.empty(self._columns)
        carry, self._carry = self._carry, np.empty((0, self._columns))
        return Bins(np.array([self._carry_t0]), carry.mean(axis=0, keepdims=True),
                    carry.min(axis=0, keepdims=True), carry.max(axis=0, keepdims=True))
