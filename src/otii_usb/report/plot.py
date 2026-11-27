# plot.py: voltage, current and power over time as a PNG
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
# PlotSink bins the recording to about 4000 points (mean, min, max).
# render_png draws three panels sharing the time axis: the mean as a line
# and min/max as a translucent band, so short spikes stay visible at any
# zoom. The title names the Otii model and serial number; below it the
# start time, duration, setpoint, sample and plot rates and firmware.
# Axes use engineering prefixes (mA, µs, ...). The figure is built with
# the object API and the Agg canvas, without pyplot's global state, so it
# can be rendered from the MCP server's worker threads (SW-010).

"""Measurement plots: voltage, current and power over time, rendered to PNG."""
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import EngFormatter
from numpy.typing import NDArray

from ..measurement.binning import Binner, Bins
from ..measurement.csv_log import MAIN_COLUMNS, ColumnSink
from ..measurement.stats import MeasurementSummary

if TYPE_CHECKING:
    from ..device.channels import ChannelInfo

DEFAULT_POINTS = 4000

_PANELS = (  # label, unit, colour
    ("Voltage", "V", "#4f46e5"),
    ("Current", "A", "#0d9488"),
    ("Power", "W", "#d97706"),
)
_INK, _MUTED, _GRID, _PAPER = "#1f2937", "#6b7280", "#e5e7eb", "#ffffff"


class PlotSink(ColumnSink):
    """Keeps mean/min/max bins (about ``points`` of them over ``duration``) for plotting."""

    def __init__(self, duration: float, *, points: int = DEFAULT_POINTS,
                 columns: Sequence[tuple[int, str]] = MAIN_COLUMNS) -> None:
        super().__init__(columns)
        self._duration = duration
        self._points = points
        self._binner: Binner | None = None
        self._parts: list[Bins] = []

    def start(self, channels: Mapping[int, "ChannelInfo"]) -> None:
        super().start(channels)
        decimation = max(1, math.ceil(self.sample_rate * self._duration / self._points))
        self._binner = Binner(self.sample_rate, decimation, len(self.columns))

    def write_block(self, sequence: int, block: NDArray[np.float64]) -> None:
        assert self._binner is not None
        bins = self._binner.feed(sequence, block)
        if len(bins):
            self._parts.append(bins)

    def finish(self) -> None:
        if self._binner is not None and len(tail := self._binner.flush()):
            self._parts.append(tail)

    @property
    def bin_rate(self) -> float:
        return self._binner.bin_rate if self._binner else 0.0

    def bins(self) -> Bins:
        return Bins.concat(self._parts, len(self.columns))


@dataclass(frozen=True, slots=True)
class PlotInfo:
    model: str
    serial_number: str
    firmware: str
    started: datetime
    duration: float
    sample_rate: int
    bin_rate: float
    voltage_setpoint: float | None = None


def _rate(hz: float) -> str:
    return EngFormatter(unit="S/s", places=0, sep=" ")(hz)


def render_png(path: str | Path, bins: Bins, info: PlotInfo, summary: MeasurementSummary) -> Path:
    """Render three stacked panels (V, I, P) sharing the time axis; returns the path."""
    path = Path(path)
    fig = Figure(figsize=(12, 8.5), dpi=150, facecolor=_PAPER, layout="constrained")
    FigureCanvasAgg(fig)
    axes = fig.subplots(3, 1, sharex=True)
    stats = (summary.voltage, summary.current, summary.power)

    for i, (ax, (label, unit, colour), st) in enumerate(zip(axes, _PANELS, stats)):
        ax.set_facecolor(_PAPER)
        if len(bins):
            if np.any(bins.maximum[:, i] > bins.minimum[:, i]):
                ax.fill_between(bins.times, bins.minimum[:, i], bins.maximum[:, i],
                                color=colour, alpha=0.18, linewidth=0, label="min / max")
            ax.plot(bins.times, bins.mean[:, i], color=colour, linewidth=1.3, label="mean")
        ax.yaxis.set_major_formatter(EngFormatter(unit=unit, sep=" "))
        ax.set_ylabel(label, color=_INK, fontsize=11, labelpad=10)
        ax.grid(True, color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(_GRID)
        ax.tick_params(colors=_MUTED, labelsize=9, length=0)
        if st is not None:
            fmt = EngFormatter(unit=unit, places=3, sep=" ")
            ax.text(0.995, 0.96, f"avg {fmt(st.mean)}   min {fmt(st.minimum)}   max {fmt(st.maximum)}",
                    transform=ax.transAxes, ha="right", va="top", fontsize=9, color=_INK,
                    bbox={"boxstyle": "round,pad=0.4", "facecolor": _PAPER,
                          "edgecolor": _GRID, "alpha": 0.9})
        if i == 0:
            ax.legend(loc="upper left", frameon=False, fontsize=8, labelcolor=_MUTED, ncols=2)

    axes[-1].xaxis.set_major_formatter(EngFormatter(unit="s", sep=" "))
    axes[-1].set_xlabel("Time", color=_INK, fontsize=11, labelpad=8)
    if info.duration > 0:
        axes[-1].set_xlim(0, info.duration)

    fig.suptitle(f"{info.model}  ·  S/N {info.serial_number}", x=0.01, ha="left",
                 fontsize=16, fontweight="bold", color=_INK)
    details = [info.started.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
               f"{info.duration:g} s", f"{_rate(info.sample_rate)} sampled",
               f"plotted at {_rate(info.bin_rate)} (mean, min/max band)", f"fw {info.firmware}"]
    if info.voltage_setpoint is not None:
        details.insert(2, f"setpoint {info.voltage_setpoint:g} V")
    if summary.missing_packets:
        details.append(f"{summary.missing_packets} packets missing")
    axes[0].set_title("   ·   ".join(details), loc="left", fontsize=9.5, color=_MUTED, pad=14)
    fig.savefig(path, facecolor=_PAPER)
    return path.resolve()
