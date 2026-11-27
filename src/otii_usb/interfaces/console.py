# console.py: terminal output - results as rich tables, plain text or
# JSON, and progress bars
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
# render() prints a CommandResult: JSON with --json, plain lines when the
# output is not a terminal (pipes, logs), coloured tables otherwise.
# progress_bar() shows an ASCII spinner, a bar, elapsed and remaining time
# for operations of known duration; it draws on stderr and only on
# terminals, so JSON output and pipes stay clean (REQ-UI-CLI-003).

"""Terminal presentation: result rendering (plain, JSON, rich) and progress bars."""
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

from rich.console import Console
from rich.progress import (BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)
from rich.table import Table
from rich.theme import Theme

from ..measurement.stats import unit_of
from ..protocol.constants import channel_name
from ..protocol.session import ProgressCallback
from .commands import CommandResult
from .common import format_si

THEME = Theme({
    "accent": "cyan", "muted": "grey50", "ok": "green", "warn": "yellow", "error": "bold red",
    "on": "bold red", "off": "bold green", "value": "bold white", "label": "grey70",
})


def make_console(*, stderr: bool = False, file: IO[str] | None = None,
                 width: int | None = None, force_terminal: bool | None = None) -> Console:
    return Console(theme=THEME, stderr=stderr, file=file, width=width, highlight=False,
                   force_terminal=force_terminal)


def _styled_value(value: str) -> str:
    if value in ("ON", "OFF"):
        return f"[{value.lower()}]{value}[/]"
    return f"[value]{value}[/]"


def render(console: Console, result: CommandResult, *, as_json: bool = False) -> None:
    if as_json:
        console.print_json(json.dumps(result.data))
        return
    if not console.is_terminal:
        console.print(_plain(result), markup=False, soft_wrap=True)
        return
    console.print(f"[ok]✔[/] [bold]{result.headline}[/]")
    if result.fields:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="label", justify="right")
        table.add_column()
        for label, value in result.fields:
            table.add_row(label, _styled_value(value))
        console.print(table)
    if result.summary is not None and result.summary.channels:
        table = Table(box=None, header_style="muted", pad_edge=False, padding=(0, 2))
        for heading in ("channel", "avg", "min", "max", "std", "samples"):
            table.add_column(heading, justify="left" if heading == "channel" else "right")
        for channel, s in result.summary.channels.items():
            unit = unit_of(channel)
            table.add_row(f"[accent]{channel_name(channel)}[/]", f"[value]{format_si(s.mean, unit)}[/]",
                          format_si(s.minimum, unit), format_si(s.maximum, unit),
                          format_si(s.std, unit), f"{s.count:,}")
        console.print(table)
    if result.warning:
        console.print(f"[warn]⚠ {result.warning}[/]")


def _plain(result: CommandResult) -> str:
    lines = [result.headline, *(f"{label}: {value}" for label, value in result.fields)]
    if result.summary is not None:
        for channel, s in result.summary.channels.items():
            unit = unit_of(channel)
            lines.append(f"{channel_name(channel):3s} avg {format_si(s.mean, unit):>14s}   "
                         f"min {format_si(s.minimum, unit):>14s}   "
                         f"max {format_si(s.maximum, unit):>14s}   n={s.count}")
    if result.warning:
        lines.append(f"warning: {result.warning}")
    return "\n".join(lines)


@contextmanager
def progress_bar(console: Console, label: str, total: float | None) -> Iterator[ProgressCallback | None]:
    """A transient spinner + bar for operations of known duration (terminals only)."""
    if total is None or total <= 0 or not console.is_terminal:
        yield None
        return
    progress = Progress(
        SpinnerColumn("line", style="accent"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=36, style="grey23", complete_style="cyan", finished_style="green",
                  pulse_style="cyan"),
        TaskProgressColumn(style="muted"),
        TimeElapsedColumn(),
        TextColumn("[muted]eta[/]"),
        TimeRemainingColumn(compact=True),
        console=console, transient=True)
    with progress:
        task = progress.add_task(label, total=total)
        yield lambda elapsed, _total: progress.update(task, completed=min(elapsed, total))
