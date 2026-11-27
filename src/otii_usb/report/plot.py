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
# render_png draws three panels sharing the time axis, power on top, then
# current and voltage (REQ-MEAS-REC-006): the mean as a line
# and min/max as a translucent band, so short spikes stay visible at any
# zoom. The title names the Otii model and serial number unless the caller
# gives one (REQ-MEAS-REC-005), drawn as plain text and stored as the PNG's
# Title; below it the start time, duration, setpoint with the current limit
# ("setpoint 5 V @ 2 A", REQ-MEAS-REC-010), sample and plot rates
# and firmware. Axes use engineering prefixes (mA, µs, ...). A power-on
# recording marks when the switch-on command was sent with a dashed line
# (REQ-MEAS-REC-004). A grey footer at the bottom right names the project
# unless the caller replaces or suppresses it (REQ-MEAS-REC-007).
# With serial terminal text a fourth panel at the bottom, labelled with the
# serial port (its last path part if longer than 40 characters), shows each line at
# its arrival: a faint solid marker and the text after it, rotated to read
# upwards in a small monospace font - the arrival time in bold green, then
# what came over the serial line in black - cut to the panel's height
# (REQ-MEAS-REC-009). Labels never overlap: a line gets a marker once the
# time axis has moved one text line height (1.2 times the font size, as in
# multi-line text) past the last marker. The lines in between follow that
# marker's label in further columns, each with its own arrival time, until
# the next column would reach the next marker; the rest are left out. The
# spacing
# is measured on the laid-out figure, which is then frozen. Times when the
# port was not open (an adapter powered by the device under test appears
# only after the switch-on) are shaded. Behind it all, a faint orange step
# curve shows the characters per second in 250 bins, on a right axis whose
# top is the busiest bin. A grey note at the bottom left gives the baud rate
# and how many lines are shown.
# The figure is built with the object API and the Agg canvas, without
# pyplot's global state, so it can be rendered from the MCP server's
# worker threads (REQ-MEAS-REC-002).

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
from ..measurement.serial_text import SerialText, character_rate
from ..measurement.stats import MeasurementSummary

if TYPE_CHECKING:
    from ..device.channels import ChannelInfo

DEFAULT_POINTS = 4000
DEFAULT_FOOTER = "created with github.com/bitqan-ae/otii-ace-mcp"

_PANELS = (  # top to bottom: column in MAIN_COLUMNS, label, unit, colour
    (2, "Power", "W", "#d97706"),
    (1, "Current", "A", "#0d9488"),
    (0, "Voltage", "V", "#4f46e5"),
)
_INK, _MUTED, _GRID, _PAPER = "#1f2937", "#6b7280", "#e5e7eb", "#ffffff"
_SERIAL, _STAMP = "#000000", "#15803d"  # serial text black; arrival times bold green
_RATE = "#d97706"                   # characters per second, faint orange
_RATE_BINS = 250                    # bins of the character rate over the recording
_TEXT_SIZE = 6.0                    # serial text [pt]
_LINE_SPACING = 1.2                 # label pitch in font sizes, as for multi-line text
_TEXT_PAD = 1.5                     # marker to text, and text to the panel's edges [pt]


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
    current_limit: float | None = None
    output_on: float | None = None
    """When the switch-on command was sent [s on the time axis], marked with a dashed line."""
    title: str | None = None
    """Replaces the default title, model and serial number."""
    footer: str | None = DEFAULT_FOOTER
    """Grey text at the bottom right; None for none."""

    @property
    def heading(self) -> str:
        return self.title or f"{self.model}  ·  S/N {self.serial_number}"


def _rate(hz: float) -> str:
    return EngFormatter(unit="S/s", places=0, sep=" ")(hz)


def spaced(times: Sequence[float], pitch: float) -> list[int]:
    """Indices of the times kept when each must be at least ``pitch`` after the last kept."""
    kept: list[int] = []
    last = -math.inf
    for index, at in enumerate(times):
        if at >= last + pitch:
            kept.append(index)
            last = at
    return kept


def place(times: Sequence[float], pitch: float, end: float) -> list[tuple[int, float, bool]]:
    """Where each label goes: (index, column position, has a marker), in time order.

    Markers are ``spaced``. The lines between two markers follow the first one's label in
    further columns, one ``pitch`` apart, as long as a column ends before the next marker
    (or ``end``); the rest are left out.
    """
    markers = spaced(times, pitch)
    placed: list[tuple[int, float, bool]] = []
    for n, marker in enumerate(markers):
        following = markers[n + 1] if n + 1 < len(markers) else len(times)
        limit = times[following] if following < len(times) else end
        placed.append((marker, times[marker], True))
        column = times[marker] + pitch
        for index in range(marker + 1, following):
            if column + pitch > limit:
                break
            placed.append((index, column, False))
            column += pitch
    return placed


def _style(ax) -> None:
    ax.set_facecolor(_PAPER)
    ax.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=9, length=0)


def _rate_axis(ax, serial: SerialText, duration: float) -> None:
    """Characters per second behind the serial text, on a right axis topped by the peak."""
    rate_ax = ax.twinx()
    ax.set_zorder(rate_ax.get_zorder() + 1)            # text and markers in front
    ax.patch.set_visible(False)
    edges, rate = character_rate(serial.characters, 0.0, duration, _RATE_BINS)
    peak = float(rate.max()) if rate.size else 0.0
    if peak > 0:
        rate_ax.stairs(rate, edges, fill=True, color=_RATE, alpha=0.12, linewidth=0)
        rate_ax.stairs(rate, edges, color=_RATE, alpha=0.35, linewidth=0.6)
    top = peak if peak > 0 else 1.0
    rate_ax.set_ylim(0, top)
    rate_ax.set_yticks([0, top / 2, top] if peak > 0 else [0])
    rate_ax.yaxis.set_major_formatter(EngFormatter(places=1, sep=" "))
    rate_ax.set_ylabel("Characters/s", color=_RATE, fontsize=9, labelpad=8)
    for spine in rate_ax.spines.values():
        spine.set_visible(False)
    rate_ax.tick_params(colors=_RATE, labelsize=8, length=0)


def _serial_panel(fig: Figure, ax, serial: SerialText) -> int:
    """Markers and vertical text for the lines that fit; returns how many are shown."""
    fig.draw_without_rendering()            # final layout, then keep it
    fig.set_layout_engine("none")
    box = ax.get_window_extent()
    left, right = ax.get_xlim()
    seconds_per_point = (right - left) / box.width * fig.dpi / 72
    probe = ax.text(0, 0, "M" * 20, fontsize=_TEXT_SIZE, family="monospace", fontweight="bold")
    advance = probe.get_window_extent().width / 20 * 72 / fig.dpi     # one character [pt]
    probe.remove()
    height = box.height * 72 / fig.dpi                                  # panel [pt]
    columns = max(1, int((height - 2 * _TEXT_PAD) / advance))
    pitch = (_LINE_SPACING * _TEXT_SIZE + _TEXT_PAD) * seconds_per_point
    closed, start = [], left                # spans without the port: shaded
    for opened, ended in serial.open:
        if opened > start:
            closed.append((start, opened))
        start = max(start, ended)
    if start < right:
        closed.append((start, right))
    for a, b in closed:
        ax.axvspan(a, b, color=_GRID, alpha=0.5, linewidth=0)
    lines = [line for line in serial.lines if left <= line.time <= right]
    shown = place([line.time for line in lines], pitch, right)
    for index, column, marked in shown:
        line = lines[index]
        if marked:
            ax.axvline(line.time, color=_MUTED, alpha=0.35, linewidth=0.6, linestyle="-")
        stamp = f"{line.time:.4f} s"
        room = columns - len(stamp) - 1
        text = line.text if len(line.text) <= room else line.text[:max(0, room - 1)] + "…"
        for offset, label, colour, weight in ((0, stamp, _STAMP, "bold"),
                                              (len(stamp) + 1, text, _SERIAL, "normal")):
            ax.annotate(label, (column, 0), xycoords=("data", "axes fraction"),
                        xytext=(_TEXT_PAD, _TEXT_PAD + offset * advance),
                        textcoords="offset points", rotation=90, ha="left", va="bottom",
                        fontsize=_TEXT_SIZE, family="monospace", color=colour,
                        fontweight=weight, annotation_clip=False, parse_math=False,
                        in_layout=False)
    if not lines:
        ax.text(0.5, 0.5, "no serial text received" if serial.open else
                f"{serial.port} was not available", transform=ax.transAxes, ha="center",
                va="center", fontsize=9, color=_MUTED, parse_math=False)
    return len(shown)


def render_png(path: str | Path, bins: Bins, info: PlotInfo, summary: MeasurementSummary,
               serial: SerialText | None = None) -> Path:
    """Render stacked panels (P, I, V, and the serial text if given) sharing the time axis.

    Returns the path.
    """
    path = Path(path)
    rows = 3 if serial is None else 4
    fig = Figure(figsize=(12, 8.5 if serial is None else 12), dpi=150, facecolor=_PAPER,
                 layout="constrained")
    FigureCanvasAgg(fig)
    axes = fig.subplots(rows, 1, sharex=True,
                        height_ratios=None if serial is None else (1, 1, 1, 1.9))
    stats = (summary.voltage, summary.current, summary.power)

    for row, (ax, (i, label, unit, colour)) in enumerate(zip(axes, _PANELS)):
        st = stats[i]
        _style(ax)
        if len(bins):
            if np.any(bins.maximum[:, i] > bins.minimum[:, i]):
                ax.fill_between(bins.times, bins.minimum[:, i], bins.maximum[:, i],
                                color=colour, alpha=0.18, linewidth=0, label="min / max")
            ax.plot(bins.times, bins.mean[:, i], color=colour, linewidth=1.3, label="mean")
        if info.output_on is not None:
            at = EngFormatter(unit="s", places=1, sep=" ")(info.output_on)
            ax.axvline(info.output_on, color=_INK, linestyle="--", linewidth=1.0,
                       label=f"switch-on sent ({at})")
        ax.yaxis.set_major_formatter(EngFormatter(unit=unit, sep=" "))
        ax.set_ylabel(label, color=_INK, fontsize=11, labelpad=10)
        if st is not None:
            fmt = EngFormatter(unit=unit, places=3, sep=" ")
            ax.text(0.995, 0.96, f"avg {fmt(st.mean)}   min {fmt(st.minimum)}   max {fmt(st.maximum)}",
                    transform=ax.transAxes, ha="right", va="top", fontsize=9, color=_INK,
                    bbox={"boxstyle": "round,pad=0.4", "facecolor": _PAPER,
                          "edgecolor": _GRID, "alpha": 0.9})
        if row == 0:
            ax.legend(loc="upper left", frameon=False, fontsize=8, labelcolor=_MUTED, ncols=4)

    if serial is not None:
        ax = axes[3]
        _style(ax)
        ax.grid(False, axis="y")
        ax.set_yticks([])
        ax.set_ylim(0, 1)
        port = serial.port if len(serial.port) <= 40 else serial.port.rsplit("/", 1)[-1]
        ax.set_ylabel(f"Serial ({port})", color=_INK, fontsize=11, labelpad=10,
                      parse_math=False)
        if info.output_on is not None:
            ax.axvline(info.output_on, color=_INK, linestyle="--", linewidth=1.0)
        if info.duration > 0:
            _rate_axis(ax, serial, info.duration)

    axes[-1].xaxis.set_major_formatter(EngFormatter(unit="s", sep=" "))
    axes[-1].set_xlabel("Time", color=_INK, fontsize=11, labelpad=8)
    if info.duration > 0:
        axes[-1].set_xlim(0, info.duration)

    fig.suptitle(info.heading, x=0.01, ha="left", fontsize=16, fontweight="bold",
                 color=_INK, parse_math=False)
    details = [info.started.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
               f"{info.duration:g} s", f"{_rate(info.sample_rate)} sampled",
               f"plotted at {_rate(info.bin_rate)} (mean, min/max band)", f"fw {info.firmware}"]
    if info.voltage_setpoint is not None:
        limit = "" if info.current_limit is None else f" @ {info.current_limit:.4g} A"
        details.insert(2, f"setpoint {info.voltage_setpoint:.4g} V{limit}")
    if summary.missing_packets:
        details.append(f"{summary.missing_packets} packets missing")
    axes[0].set_title("   ·   ".join(details), loc="left", fontsize=9.5, color=_MUTED, pad=14)
    if info.footer:
        fig.text(0.995, 0.004, info.footer, ha="right", va="bottom", fontsize=8,
                 color=_MUTED, parse_math=False)
    if serial is not None:
        shown = _serial_panel(fig, axes[3], serial)
        total = sum(1 for line in serial.lines if 0 <= line.time <= info.duration)
        count = (f"{total} lines" if shown == total else
                 f"{shown} of {total} lines shown, the others would overlap")
        fig.text(0.005, 0.004, f"serial {serial.baud} Bd 8N1   ·   {count}", ha="left",
                 va="bottom", fontsize=8, color=_MUTED)
    fig.savefig(path, facecolor=_PAPER, metadata={"Title": info.heading})
    return path.resolve()
