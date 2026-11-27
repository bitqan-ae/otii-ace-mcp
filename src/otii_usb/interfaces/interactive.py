# interactive.py: the interactive shell (otii-usb -i)
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
# Built on prompt_toolkit and rich: a banner, a prompt that shows the model
# and the main output state (red ON, green OFF), syntax highlighting of the
# input line, completion of commands, options and file names, persistent
# history with auto-suggestion (~/.local/state/otii-usb/history) and help
# with examples for every command (SW-013).
#
# Safety: the safe state is applied when the shell starts (SAF-001); Ctrl-C
# during a command stops it and switches the outputs off, and the session
# continues (SAF-016); exit, quit, Ctrl-D, SIGTERM and SIGHUP leave with
# the outputs off (SAF-002); an unsupported device is refused (SAF-008).

"""Interactive shell: ``otii-usb -i``."""
import argparse
import os
import re
import shlex
import signal
import sys
import termios
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, NestedCompleter, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table

from ..errors import OtiiError, UnsupportedDeviceError
from .banner import print_banner
from .commands import COMMANDS, COMMANDS_BY_NAME, add_command_parsers
from .console import make_console, progress_bar, render
from .gateway import DeviceGateway
from .lifecycle import TERMINATION_SIGNALS, SafeExit, signals_shielded

EXIT_UNSUPPORTED = 2
BUILTINS: dict[str, str] = {
    "help": "list commands, or 'help COMMAND' for options and examples",
    "clear": "clear the screen",
    "exit": "switch the outputs off and leave (also: quit, Ctrl-D)",
    "quit": "same as exit",
}
_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE]-?\d+)?$")

STYLE = Style.from_dict({
    "command": "#22d3ee bold", "builtin": "#22d3ee", "option": "#a78bfa", "number": "#fbbf24",
    "arg": "#e5e7eb", "unknown": "#f87171 underline",
    "prompt.name": "#22d3ee bold", "prompt.model": "#6b7280",
    "prompt.on": "#f87171 bold", "prompt.off": "#34d399 bold", "prompt.unknown": "#6b7280",
    "prompt.arrow": "#a78bfa bold",
    "auto-suggestion": "#4b5563",
})


def default_history_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "otii-usb" / "history"


def build_shell_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="", add_help=False, exit_on_error=False)
    sub = parser.add_subparsers(dest="name", metavar="COMMAND")
    add_command_parsers(sub, example_prefix="")
    return parser


class CommandLexer(Lexer):
    """Colours the input line: command, options, numbers, arguments, unknown commands."""

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        def line(number: int) -> StyleAndTextTuples:
            text = document.lines[number]
            tokens: StyleAndTextTuples = []
            first = True
            for part in re.split(r"(\s+)", text):
                if not part or part.isspace():
                    tokens.append(("", part))
                    continue
                if first:
                    style = ("class:command" if part in COMMANDS_BY_NAME else
                             "class:builtin" if part in BUILTINS else "class:unknown")
                    first = False
                elif part.startswith("-") and not _NUMBER.match(part):
                    style = "class:option"
                elif _NUMBER.match(part):
                    style = "class:number"
                else:
                    style = "class:arg"
                tokens.append((style, part))
            return tokens
        return line


def build_completer(parser: argparse.ArgumentParser) -> Completer:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    tree: dict[str, object] = {}
    for name, command_parser in sub.choices.items():
        options: dict[str, object] = {}
        for action in command_parser._actions:
            for option in action.option_strings:
                options[option] = PathCompleter(expanduser=True) if action.metavar == "FILE" else None
            if not action.option_strings and action.choices:
                options.update({choice: None for choice in action.choices})
        options.setdefault("--help", None)
        tree[name] = options
    tree["help"] = {name: None for name in sub.choices}
    tree.update({name: None for name in BUILTINS if name != "help"})
    return NestedCompleter.from_nested_dict(tree)


class InteractiveShell:
    def __init__(self, gateway: DeviceGateway, *, version: str, console: Console | None = None,
                 history: History | None = None, prompt_io: dict | None = None) -> None:
        self.gateway = gateway
        self.version = version
        self.console = console or make_console()
        self.err = make_console(stderr=True) if console is None else console
        self.parser = build_shell_parser()
        self.output_on: bool | None = None
        self.model = "Otii"
        self._history = history
        self._prompt_io = prompt_io or {}
        self._running = True

    # ------------------------------------------------------------ main loop

    def run(self) -> int:
        print_banner(self.console, self.version)
        try:
            report = self.gateway.ensure_safe()
            self._refresh_identity()
        except UnsupportedDeviceError as e:
            self.err.print(f"[error]✖ {e}[/]\n[muted]refusing to start; the device was not "
                           "modified[/]")
            return EXIT_UNSUPPORTED
        except OtiiError as e:
            self.err.print(f"[warn]⚠ device not ready: {e}[/]")
        else:
            if report:
                self.console.print(
                    f"[ok]✔[/] safe state: outputs [off]OFF[/], "
                    f"{report['voltage_setpoint_v']:g} V setpoint, "
                    f"{report['current_limit_a'] * 1000:g} mA limit\n")

        saved_tty = _save_terminal()
        safe_exit = SafeExit(self.gateway.shutdown, signals=TERMINATION_SIGNALS,
                             before_exit=lambda: _restore_terminal(saved_tty))
        safe_exit.install()
        session = PromptSession(history=self._history or self._file_history(),
                                completer=build_completer(self.parser), lexer=CommandLexer(),
                                style=STYLE, auto_suggest=AutoSuggestFromHistory(),
                                complete_while_typing=True, **self._prompt_io)
        try:
            while self._running:
                try:
                    line = session.prompt(self._prompt)
                except KeyboardInterrupt:
                    continue                              # Ctrl-C at the prompt: new line
                except EOFError:
                    break
                self.execute(line)
        finally:
            with signals_shielded():
                self.console.print("[muted]switching outputs off ...[/]")
                report = self.gateway.shutdown()
                self.console.print("[ok]✔[/] outputs [off]OFF[/], defaults restored. bye."
                                   if report else "[error]✖ could not reach the device to "
                                   "switch the outputs off[/]")
        return 0

    def _file_history(self) -> History:
        path = default_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return FileHistory(str(path))
        except OSError:
            return InMemoryHistory()

    def _prompt(self) -> StyleAndTextTuples:
        state = {True: ("class:prompt.on", "● ON"), False: ("class:prompt.off", "○ OFF"),
                 None: ("class:prompt.unknown", "?")}[self.output_on]
        return [("class:prompt.name", "otii"), ("", " "),
                ("class:prompt.model", self.model.removeprefix("Otii ")),
                ("", " "), state, ("class:prompt.arrow", " ❯ ")]

    def _refresh_identity(self) -> None:
        info = self.gateway.call(lambda d: d.info())
        self.model = info.model
        self.output_on = info.output_enabled
        self.console.print(f"[accent]{info.model}[/]  [muted]S/N {info.serial_number} · "
                           f"fw {info.firmware} · {info.port}[/]")

    def _refresh_output(self) -> None:
        try:
            self.output_on = self.gateway.call(lambda d: d.output_enabled)
        except (OtiiError, RuntimeError):
            self.output_on = None

    # ------------------------------------------------------------ commands

    def execute(self, line: str) -> None:
        try:
            words = shlex.split(line)
        except ValueError as e:
            self.err.print(f"[error]✖ {e}[/]")
            return
        if not words:
            return
        name = words[0]
        if name in ("exit", "quit"):
            self._running = False
            return
        if name == "clear":
            self.console.clear()
            return
        if name == "help":
            self.show_help(words[1] if len(words) > 1 else None)
            return
        if name not in COMMANDS_BY_NAME:
            self.err.print(f"[error]✖ unknown command '{name}'[/] [muted]- type 'help'[/]")
            return
        try:
            args = self.parser.parse_args(words)
        except SystemExit:                       # --help, or argparse printed an error
            return
        except argparse.ArgumentError as e:
            self.err.print(f"[error]✖ {e}[/]")
            return
        self._run(args)

    def _run(self, args: argparse.Namespace) -> None:
        command = args.command
        try:
            with progress_bar(self.err, command.progress_label or command.name,
                              command.duration(args)) as progress:
                result = self.gateway.call(lambda d: command.run(d, args, progress))
            render(self.console, result)
        except KeyboardInterrupt:
            with signals_shielded():
                self.err.print("[warn]⚠ interrupted - switching outputs off ...[/]")
                report = self.gateway.restore_safe_state()
                self.err.print("[ok]✔[/] outputs [off]OFF[/], defaults restored" if report else
                               "[error]✖ could not reach the device[/]")
        except (OtiiError, ValueError, PermissionError, RuntimeError) as e:
            self.err.print(f"[error]✖ {e}[/]")
        self._refresh_output()

    def show_help(self, name: str | None = None) -> None:
        if name in COMMANDS_BY_NAME:
            command = COMMANDS_BY_NAME[name]
            sub = next(a for a in self.parser._actions
                       if isinstance(a, argparse._SubParsersAction))
            self.console.print(sub.choices[name].format_help(), markup=False, highlight=False)
            return
        if name is not None and name not in BUILTINS:
            self.err.print(f"[error]✖ unknown command '{name}'[/]")
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column(style="accent")
        table.add_column()
        table.add_column(style="muted")
        for command in COMMANDS:
            example = command.examples[-1][0] if len(command.examples) > 1 else ""
            table.add_row(command.name, command.help, f"e.g. {example}" if example else "")
        for builtin, text in BUILTINS.items():
            table.add_row(builtin, text, "")
        self.console.print(table)
        self.console.print("[muted]help COMMAND shows all options and examples · Tab completes · "
                           "↑ history · Ctrl-C stops a running command and switches the "
                           "outputs off[/]")


def _save_terminal() -> list | None:
    try:
        return termios.tcgetattr(sys.stdin.fileno()) if sys.stdin.isatty() else None
    except (termios.error, OSError, ValueError):
        return None


def _restore_terminal(saved: list | None) -> None:
    if saved is not None:
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
        except (termios.error, OSError, ValueError):
            pass


def run_interactive(gateway: DeviceGateway, version: str) -> int:
    if not sys.stdin.isatty():
        print("interactive mode needs a terminal", file=sys.stderr)
        return 2
    # Ctrl-C during a command must raise KeyboardInterrupt even if the shell started us
    # with SIGINT ignored.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    return InteractiveShell(gateway, version=version).run()
