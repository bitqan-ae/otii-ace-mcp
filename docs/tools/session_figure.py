# session_figure.py: a real interactive session as SVG and PNG
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
# Usage (from the repository root, with the venv active):
#
#   python docs/tools/session_figure.py capture --serial /dev/ttyUSB0
#   python docs/tools/session_figure.py
#
# "capture" runs `otii-usb -i` in a detached tmux pane of fixed size on an
# attached Otii Ace Pro and types the session in commands(): it POWERS THE
# DEVICE UNDER TEST (record --power-on), with the serial console given by
# --serial. It waits for each command's result, not for fixed times, and
# keeps its history in a temporary directory. The pane's final screen,
# colours included, goes to docs/tools/session-capture.ansi with the
# device's serial numbers and the working directory masked as in the README
# (DC-TOOL-DOC-004); the capture fails if one is left.
#
# Without arguments the capture is drawn as an SVG in a temporary directory -
# rich splits it into coloured runs, each placed at its own column in
# DejaVu Sans Mono, so neither font substitution nor whitespace handling can
# shift the columns - and rendered at twice the size to
# docs/images/session.png (needs rsvg-convert from librsvg); only the PNG is
# kept, so the figure is made from the capture alone. The plot the session
# records is ZeSDR-boot.png in the repository root.

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from html import escape
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI
from rich.text import Text

ROOT = Path(__file__).resolve().parents[2]
CAPTURE = Path(__file__).with_name("session-capture.ansi")
IMAGES = ROOT / "docs" / "images"
TMUX_SESSION = "otii-session-figure"
WIDTH, HEIGHT = 150, 45
PROMPT = "❯"


def commands(serial: str) -> list[tuple[str, str]]:
    """What is typed, and the text that shows it finished."""
    return [
        ("safe --current-limit 2", "current limit"),
        ("record --png ZeSDR-boot.png -t 2 --power-on --pre 0 --raw "
         f'--title "ZeSDR Zynq-7000 Boot Process" --serial {serial}', "mp "),
        ("exit", "bye"),
    ]


def tmux(*args: str) -> str:
    return subprocess.run(["tmux", *args], check=True, capture_output=True, text=True).stdout


def screen(colours: bool = False) -> str:
    """The pane's text from the start of its history."""
    return tmux("capture-pane", "-p", "-S", "-", *(["-e"] if colours else []),
                "-t", TMUX_SESSION)


def wait_for(text: str, after: int, timeout: float = 60.0) -> None:
    """Until ``text`` appears after the first ``after`` characters of the screen."""
    deadline = time.monotonic() + timeout
    while text not in screen()[after:]:
        if time.monotonic() > deadline:
            raise TimeoutError(f"{text!r} did not appear:\n{screen()}")
        time.sleep(0.05)


def identity(otii: Path) -> list[str]:
    status = json.loads(subprocess.run([str(otii), "--json", "status"], check=True,
                                       capture_output=True, text=True).stdout)
    return [status["serial_number"], status["board_serial"]]


def capture(serial: str) -> None:
    otii = ROOT / ".venv" / "bin" / "otii-usb"
    secrets = identity(otii)
    with tempfile.TemporaryDirectory() as state:
        tmux("new-session", "-d", "-s", TMUX_SESSION, "-x", str(WIDTH), "-y", str(HEIGHT),
             "-c", str(ROOT), "-e", f"XDG_STATE_HOME={state}", "-e", "TERM=xterm-256color",
             f"{otii} -i")
        try:
            tmux("set-option", "-t", TMUX_SESSION, "remain-on-exit", "on")
            wait_for(PROMPT, 0)
            for line, done in commands(serial):
                seen = len(screen())
                tmux("send-keys", "-t", TMUX_SESSION, "-l", line)
                tmux("send-keys", "-t", TMUX_SESSION, "Enter")
                wait_for(done, seen)
            wait_for("Pane is dead", 0)
            text = screen(colours=True)
        finally:
            subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION], capture_output=True)
    text = "\n".join(line for line in text.splitlines() if "Pane is dead" not in line).rstrip()
    replacements = [(secrets[0], "<serial>"), (secrets[1], "<board serial>"),
                    (f"{ROOT}/", "~/")]
    for secret, placeholder in replacements:
        text = text.replace(secret, placeholder)
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    left = [s for s, _ in replacements if s in plain] + \
        ([str(Path.home())] if str(Path.home()) in plain else [])
    if left:
        sys.exit("identifying text left in the capture; not saved")
    CAPTURE.write_text(text + "\n", encoding="utf-8")
    print(CAPTURE.relative_to(ROOT))


FONT = "DejaVu Sans Mono, Menlo, Consolas, monospace"
SIZE, CELL, LINE = 14.0, 14.0 * 0.6021, 14.0 * 1.35    # px; DejaVu Sans Mono's advance
PAD, BAR = 16.0, 36.0


def _hex(triplet) -> str:
    return "#{:02x}{:02x}{:02x}".format(*triplet)


def render() -> None:
    """Every coloured run at its own column, so no font or whitespace rule can shift it."""
    console = Console(width=400, file=io.StringIO(), force_terminal=True,
                      color_system="truecolor")
    lines = Text.from_ansi(CAPTURE.read_text(encoding="utf-8")).split("\n")
    columns = max(line.cell_len for line in lines)
    width, height = 2 * PAD + columns * CELL, BAR + 2 * PAD + len(lines) * LINE
    background, foreground = _hex(MONOKAI.background_color), _hex(MONOKAI.foreground_color)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
           f'height="{height:.0f}" viewBox="0 0 {width:.1f} {height:.1f}">',
           f'<rect width="100%" height="100%" rx="10" fill="{background}"/>',
           *(f'<circle cx="{20 + 22 * i}" cy="18" r="6" fill="{c}"/>'
             for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))),
           f'<text x="{width / 2:.1f}" y="23" fill="#9ca3af" font-family="{FONT}" '
           f'font-size="13" text-anchor="middle">otii-usb -i</text>',
           f'<g font-family="{FONT}" font-size="{SIZE}" xml:space="preserve">']
    for row, line in enumerate(lines):
        y = BAR + PAD / 2 + (row + 0.8) * LINE
        column = 0
        for segment in line.render(console):
            cells = segment.cell_length
            if segment.text.strip():
                style = segment.style
                colour = foreground
                if style is not None and style.color is not None:
                    colour = _hex(style.color.get_truecolor(MONOKAI))
                attributes = ""
                if style is not None and style.bold:
                    attributes += ' font-weight="bold"'
                if style is not None and style.dim:
                    attributes += ' opacity="0.6"'
                out.append(f'<text x="{PAD + column * CELL:.2f}" y="{y:.2f}" '
                           f'fill="{colour}"{attributes}>{escape(segment.text)}</text>')
            column += cells
    out += ["</g>", "</svg>"]
    tool = shutil.which("rsvg-convert")
    if tool is None:
        sys.exit("rsvg-convert not found: PNG not rendered")
    png = IMAGES / "session.png"
    with tempfile.TemporaryDirectory() as scratch:      # the SVG is only a step
        svg = Path(scratch) / "session.svg"
        svg.write_text("\n".join(out) + "\n", encoding="utf-8")
        subprocess.run([tool, "-z", "2", "-o", str(png), str(svg)], check=True)
    print(png.relative_to(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action")
    run = sub.add_parser("capture", help="run the session on the hardware (powers the DUT)")
    run.add_argument("--serial", required=True, help="serial console of the device under test")
    args = parser.parse_args()
    if args.action == "capture":
        if shutil.which("tmux") is None:
            sys.exit("tmux not found")
        capture(args.serial)
    render()


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
