# banner.py: the interactive shell's start-up banner
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
# Assembles "OTII ACE" from five-line ASCII glyphs and prints it with a
# cyan-to-violet gradient, followed by the product line and a hint.

"""Start-up banner for the interactive shell."""
from rich.console import Console
from rich.text import Text

_GLYPHS: dict[str, tuple[str, ...]] = {
    "O": ("  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "),
    "T": (" _____ ", "|_   _|", "  | |  ", "  | |  ", "  |_|  "),
    "I": (" ___ ", "|_ _|", " | | ", " | | ", "|___|"),
    "A": ("    _    ", "   / \\   ", "  / _ \\  ", " / ___ \\ ", "/_/   \\_\\"),
    "C": ("  ____ ", " / ___|", "| |    ", "| |___ ", " \\____|"),
    "E": (" _____ ", "| ____|", "|  _|  ", "| |___ ", "|_____|"),
    " ": ("   ",) * 5,
}
_GRADIENT = ("#22d3ee", "#38bdf8", "#60a5fa", "#818cf8", "#a78bfa")


def ascii_art(word: str) -> list[str]:
    rows = ["" for _ in range(5)]
    for char in word.upper():
        for i, part in enumerate(_GLYPHS[char]):
            rows[i] += part
    return [row.rstrip() for row in rows]


def print_banner(console: Console, version: str) -> None:
    for line, colour in zip(ascii_art("OTII ACE"), _GRADIENT):
        console.print(Text(line, style=f"bold {colour}"))
    console.print(Text.assemble(
        ("  Otii Ace Pro", "bold"), ("  ·  ", "grey50"), ("USB control & measurement", "grey70"),
        ("  ·  ", "grey50"), (f"otii-usb {version}", "grey50")))
    console.print(Text("  type 'help' for commands, Tab to complete, Ctrl-D to exit",
                       style="grey50"))
    console.print()
