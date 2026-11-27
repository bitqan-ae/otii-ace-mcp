# csv_log.py: CSV files of voltage, current and power for later analysis
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
# ColumnSink aligns the main channels into columns and keeps running
# statistics over the full-rate samples. CsvSink writes the header
# time_s,voltage_v,current_a,power_w and one row per bin: averaged to the
# chosen row rate (default 1000 rows per second) or every sample. Rows are
# written as data arrives, so memory does not grow with the length of the
# recording (DC-ARCH-PKG-006), and the file is complete when the recording
# is interrupted (REQ-MEAS-REC-001). A power-on recording adds the column
# output_on: 0, then 1 from the row whose bin contains the time the
# switch-on command was sent
# (REQ-MEAS-REC-004).

"""CSV logging of voltage, current and power for later analysis."""
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, TextIO

import numpy as np
from numpy.typing import NDArray

from .binning import Binner, Bins
from .sinks import SampleSink
from .stats import MAIN_COLUMNS, MeasurementSummary, RunningStats

if TYPE_CHECKING:
    from ..device.channels import ChannelInfo

DEFAULT_ROW_RATE = 1000.0


class ColumnSink(SampleSink):
    """Base for sinks that need the main channels as aligned columns at one sample rate."""

    def __init__(self, columns: Sequence[tuple[int, str]] = MAIN_COLUMNS) -> None:
        self.columns = tuple(columns)
        self.sample_rate = 0
        self.stats: dict[int, RunningStats] = {}

    def start(self, channels: Mapping[int, "ChannelInfo"]) -> None:
        missing = [name for ch, name in self.columns if ch not in channels]
        if missing:
            raise ValueError(f"columns without a recorded channel: {', '.join(missing)}")
        rates = {channels[ch].sample_rate for ch, _ in self.columns}
        if len(rates) != 1:
            raise ValueError(f"columns have different sample rates: {sorted(rates)}")
        self.sample_rate = rates.pop()
        self.stats = {ch: RunningStats() for ch, _ in self.columns}

    def write(self, sequence: int, samples: Mapping[int, NDArray[np.float64]]) -> None:
        if any(ch not in samples for ch, _ in self.columns):
            return
        n = min(samples[ch].size for ch, _ in self.columns)
        block = np.column_stack([samples[ch][:n] for ch, _ in self.columns])
        for (ch, _), column in zip(self.columns, block.T):
            self.stats[ch].update(column)
        self.write_block(sequence, block)

    def write_block(self, sequence: int, block: NDArray[np.float64]) -> None:
        """Aligned samples of one packet, shape ``(n, len(columns))``."""

    def summary(self, duration: float, missing_packets: int = 0) -> MeasurementSummary:
        stats = {ch: s for ch, rs in self.stats.items() if (s := rs.result()) is not None}
        return MeasurementSummary(duration, stats, missing_packets)


class CsvSink(ColumnSink):
    """Writes ``time_s,voltage_v,current_a,power_w`` rows.

    ``row_rate`` rows per second, each the mean of its bin; ``row_rate=None`` writes every
    sample. ``time_s`` is the bin start relative to the first packet. Statistics are
    accumulated over the full-rate samples. With ``output_on`` (when the switch-on command
    was sent, None before) a last column ``output_on`` is 0 before that time and 1 from the
    row whose bin contains it.
    """

    def __init__(self, stream: TextIO, *, row_rate: float | None = DEFAULT_ROW_RATE,
                 columns: Sequence[tuple[int, str]] = MAIN_COLUMNS, precision: int = 9,
                 output_on: Callable[[], float | None] | None = None) -> None:
        super().__init__(columns)
        if row_rate is not None and row_rate <= 0:
            raise ValueError("row rate must be positive")
        self._stream = stream
        self._requested_rate = row_rate
        self._output_on = output_on
        self._fmt = ",".join(["%.9f"] + [f"%.{precision}g"] * len(self.columns)
                             + (["%d"] if output_on is not None else []))
        self._binner: Binner | None = None
        self.rows = 0

    @property
    def decimation(self) -> int:
        return self._binner.decimation if self._binner else 1

    @property
    def row_rate(self) -> float:
        return self._binner.bin_rate if self._binner else 0.0

    def start(self, channels: Mapping[int, "ChannelInfo"]) -> None:
        super().start(channels)
        decimation = 1 if self._requested_rate is None else \
            max(1, round(self.sample_rate / self._requested_rate))
        self._binner = Binner(self.sample_rate, decimation, len(self.columns))
        names = ["time_s", *(name for _, name in self.columns)]
        if self._output_on is not None:
            names.append("output_on")
        self._stream.write(",".join(names) + "\n")

    def write_block(self, sequence: int, block: NDArray[np.float64]) -> None:
        assert self._binner is not None
        self._emit(self._binner.feed(sequence, block))

    def _emit(self, bins: Bins) -> None:
        if len(bins):
            columns = [bins.times, bins.mean]
            if self._output_on is not None:
                sent = self._output_on()
                bin_end = bins.times + self.decimation / self.sample_rate
                columns.append(np.zeros(len(bins)) if sent is None else bin_end > sent)
            np.savetxt(self._stream, np.column_stack(columns), fmt=self._fmt, delimiter=",")
            self.rows += len(bins)

    def finish(self) -> None:
        if self._binner is not None:
            self._emit(self._binner.flush())
        self._stream.flush()
