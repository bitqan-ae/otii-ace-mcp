# commands.py: the command catalogue shared by the command line and
# the interactive shell
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
# Each Command has a one-line help, a longer description, examples, its
# argparse options, how long it runs (for the progress bar) and a runner
# that returns a CommandResult (data for JSON, a headline, key/value fields
# and optional statistics). The same entries build the otii-usb
# subcommands, the shell's parser, its help and its completion, so help and
# examples exist for every command (REQ-UI-CLI-002, DC-ARCH-PKG-007).

"""Device commands shared by the one-shot CLI and the interactive shell."""
import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..device.models import SafeDefaults, on_off
from ..device.otii import DEFAULT_PRE_TRIGGER, OtiiDevice
from ..measurement.csv_log import DEFAULT_ROW_RATE
from ..measurement.stats import MeasurementSummary
from ..protocol.session import ProgressCallback
from ..report.plot import DEFAULT_FOOTER
from ..transport.uart import DEFAULT_BAUD, baud_constant

AUTO = "auto"


@dataclass(frozen=True)
class CommandResult:
    data: dict
    headline: str
    fields: tuple[tuple[str, str], ...] = ()
    summary: MeasurementSummary | None = None
    warning: str | None = None


type Runner = Callable[[OtiiDevice, argparse.Namespace, ProgressCallback | None], CommandResult]


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    description: str
    run: Runner
    examples: tuple[tuple[str, str], ...] = ()
    configure: Callable[[argparse.ArgumentParser], None] = lambda parser: None
    duration: Callable[[argparse.Namespace], float | None] = lambda args: None
    progress_label: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def epilog(self, prefix: str) -> str:
        if not self.examples:
            return ""
        width = max(len(prefix + cmd) for cmd, _ in self.examples) + 3
        lines = [f"  {prefix + cmd:<{width}}{text}" for cmd, text in self.examples]
        return "examples:\n" + "\n".join(lines)


def _on_off(value: bool) -> str:
    return on_off(value).upper()


# ---------------------------------------------------------------- runners

def run_status(dev: OtiiDevice, args: argparse.Namespace, progress) -> CommandResult:
    info = dev.info()
    return CommandResult(info.to_dict(), f"{info.model} on {info.port}", (
        ("serial number", info.serial_number), ("board serial", info.board_serial),
        ("hardware", info.hardware_revision),
        ("firmware", f"{info.firmware} (bootloader {info.bootloader})"),
        ("protocol", f"version {info.protocol_version}, max payload {info.max_payload} bytes"),
        ("main output", _on_off(info.output_enabled)),
        ("voltage setpoint", f"{info.voltage_setpoint:.4f} V"),
        ("current limit", f"{info.current_limit:.4f} A")))


def run_measure(dev: OtiiDevice, args: argparse.Namespace, progress) -> CommandResult:
    output = dev.output_enabled
    summary = dev.measure(args.time, progress=progress)
    return CommandResult({"output": "on" if output else "off", **summary.to_dict()},
                         f"main output {_on_off(output)}, {args.time:g} s window",
                         summary=summary, warning=_missing(summary))


def _missing(summary: MeasurementSummary) -> str | None:
    return f"{summary.missing_packets} data packets missing" if summary.missing_packets else None


def _record_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    csv, png = args.csv, args.png
    if csv is None and png is None:
        csv = png = AUTO
    stem = Path(datetime.now().strftime("otii-%Y%m%d-%H%M%S"))
    return (None if csv is None else stem.with_suffix(".csv") if csv == AUTO else Path(csv),
            None if png is None else stem.with_suffix(".png") if png == AUTO else Path(png))


def run_record(dev: OtiiDevice, args: argparse.Namespace, progress) -> CommandResult:
    csv, png = _record_paths(args)
    result = dev.capture(args.time, csv=csv, png=png, row_rate=None if args.raw else args.rate,
                         progress=progress, power_on=args.power_on, pre_trigger=args.pre,
                         title=args.title, footer=args.footer, serial_port=args.serial,
                         baud=args.baud)
    fields = []
    warnings = [w for w in (_missing(result.summary),
                            result.power_on.warning if result.power_on else None,
                            f"serial terminal: {result.serial.error}"
                            if result.serial and result.serial.error else None) if w]
    if (event := result.power_on) is not None:
        fields.append(("power on", f"switch-on sent at {event.command_sent * 1e3:.0f} ms, "
                                   "output OFF after"))
    if result.csv_path:
        fields.append(("csv", f"{result.csv_path}  ({result.csv_rows} rows, "
                              f"{result.csv_row_rate:g} rows/s)"))
    if result.png_path:
        fields.append(("png", str(result.png_path)))
    if (serial := result.serial) is not None:
        opened = (f", open from {serial.open[0][0] * 1e3:.0f} ms" if serial.open
                  else ", never open")
        fields.append(("serial", f"{serial.port} at {serial.baud} Bd: {len(serial.lines)} lines, "
                                 f"{serial.bytes} bytes{opened}"))
    return CommandResult(result.to_dict(), f"recorded {args.time:g} s", tuple(fields),
                         summary=result.summary, warning="; ".join(warnings) or None)


def run_output(dev: OtiiDevice, args: argparse.Namespace, progress) -> CommandResult:
    change = dev.set_output(args.state == "on")
    d = change.to_dict()
    return CommandResult(d, f"main output {d['before'].upper()} -> {d['after'].upper()}")


def run_power_cycle(dev: OtiiDevice, args: argparse.Namespace, progress) -> CommandResult:
    d = dev.power_cycle(args.off, progress=progress).to_dict()
    return CommandResult(d, f"main output {d['before'].upper()} -> OFF for {args.off:g} s -> "
                            f"{d['after'].upper()}")


def run_safe(dev: OtiiDevice, args: argparse.Namespace, progress) -> CommandResult:
    report = dev.apply_safe_defaults(SafeDefaults(voltage=args.voltage,
                                                  current_limit=args.current_limit))
    d = report.to_dict()
    return CommandResult(d, "safe state applied", (
        ("main output", d["main_output"].upper()),
        ("expansion output", f"{d['expansion_output_voltage_v']:g} V"),
        ("voltage setpoint", f"{d['voltage_setpoint_v']:.4f} V"),
        ("current limit", f"{d['current_limit_a']:.4f} A")))


# ---------------------------------------------------------------- argument setup

def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _not_negative(value: str) -> float:
    number = float(value)
    if not number >= 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return number


def _baud(value: str) -> int:
    try:
        baud = int(value)
        baud_constant(baud)
    except ValueError:
        raise argparse.ArgumentTypeError("not a standard baud rate") from None
    return baud


def _text(value: str) -> str:
    if not (text := value.strip()):
        raise argparse.ArgumentTypeError("must not be empty")
    return text


def _time_option(default: float, what: str) -> Callable[[argparse.ArgumentParser], None]:
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("-t", "--time", type=_positive, default=default, metavar="SECONDS",
                            help=f"{what} (default {default:g} s)")
    return configure


def _configure_record(parser: argparse.ArgumentParser) -> None:
    _time_option(10.0, "recording duration")(parser)
    parser.add_argument("--csv", nargs="?", const=AUTO, metavar="FILE",
                        help="write a CSV file (default name: otii-<timestamp>.csv)")
    parser.add_argument("--png", nargs="?", const=AUTO, metavar="FILE",
                        help="write a PNG plot (default name: otii-<timestamp>.png)")
    parser.add_argument("--title", type=_text, metavar="TEXT",
                        help="plot title (default: Otii model and serial number)")
    footer = parser.add_mutually_exclusive_group()
    footer.add_argument("--footer", type=_text, default=DEFAULT_FOOTER, metavar="TEXT",
                        help=f'grey note at the bottom right of the plot '
                             f'(default "{DEFAULT_FOOTER}")')
    footer.add_argument("--no-footer", dest="footer", action="store_const", const=None,
                        help="plot without the note")
    rate = parser.add_mutually_exclusive_group()
    rate.add_argument("--rate", type=_positive, default=DEFAULT_ROW_RATE, metavar="HZ",
                      help=f"CSV rows per second, averaged (default {DEFAULT_ROW_RATE:g})")
    rate.add_argument("--raw", action="store_true", help="CSV with every sample (50 kHz)")
    parser.add_argument("--power-on", action="store_true",
                        help="switch the main output on during the recording (it must be off) "
                             "and off at the end, to capture the power-up")
    parser.add_argument("--pre", type=_not_negative, default=DEFAULT_PRE_TRIGGER,
                        metavar="SECONDS", help="with --power-on: time recorded before switching "
                                                f"on (default {DEFAULT_PRE_TRIGGER:g} s)")
    parser.add_argument("--serial", metavar="PORT",
                        help="serial console of the device under test (e.g. /dev/ttyUSB0), "
                             "read only: its lines are shown at their arrival in a fourth "
                             "plot panel")
    parser.add_argument("--baud", type=_baud, default=DEFAULT_BAUD, metavar="RATE",
                        help=f"with --serial: baud rate, 8N1 (default {DEFAULT_BAUD})")


def _configure_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("state", choices=("on", "off"), help="new main output state")


def _configure_power_cycle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--off", type=_positive, default=1.0, metavar="SECONDS",
                        help="off time (default 1 s)")


def _configure_safe(parser: argparse.ArgumentParser) -> None:
    defaults = SafeDefaults()
    parser.add_argument("--voltage", type=_positive, default=defaults.voltage, metavar="VOLTS",
                        help=f"voltage setpoint (default {defaults.voltage:g} V)")
    parser.add_argument("--current-limit", type=_positive, default=defaults.current_limit,
                        metavar="AMPS", help=f"current limit (default {defaults.current_limit:g} A)")


COMMANDS: tuple[Command, ...] = (
    Command("status", "show device identity, output state, setpoint and current limit",
            "Read-only. Shows model, serial number, firmware, whether the main output is on, "
            "the voltage setpoint and the current limit.",
            run_status, examples=(("status", "show the device state"),)),
    Command("measure", "average/min/max of voltage, current and power",
            "Read-only. Records the main channels for a short window and prints statistics. "
            "The output state is not changed: with the output off you see the idle readings.",
            run_measure, examples=(
                ("measure", "0.5 s window"),
                ("measure -t 5", "5 s window"),
            ), configure=_time_option(0.5, "measurement window"),
            duration=lambda a: a.time, progress_label="measuring"),
    Command("record", "record voltage, current and power to CSV and/or a PNG plot",
            "Records the main channels for --time seconds. --csv writes time_s, voltage_v, "
            "current_a, power_w rows (averaged to --rate rows per second, or every sample with "
            "--raw); --png renders a plot over time titled with the Otii model and serial "
            "number, or with --title, and a grey project note at the bottom right (--footer "
            "replaces it, --no-footer leaves it out). Both outputs come from the same "
            "recording. Without --csv/--png both are written with timestamped names. "
            "--power-on switches the main output on --pre seconds into the recording, so the "
            "power-up is recorded, and off at the end (also on errors); the time the command "
            "was sent is marked in the plot and in the CSV column output_on. --serial reads the "
            "serial console of the device under test at --baud (8N1, nothing is sent to it) "
            "and adds a fourth plot panel: each line marked at the arrival of its first "
            "printable character, with its text written upwards behind the marker; lines that "
            "would overlap the previous one are left out.",
            run_record, examples=(
                ("record", "10 s to otii-<timestamp>.csv and .png"),
                ("record -t 60 --csv run.csv", "only a CSV, 60 s, 1000 rows/s"),
                ("record -t 5 --png boot.png", "only a plot"),
                ('record -t 5 --png --title "Sensor board, boot"', "plot with your own title"),
                ('record -t 5 --png --footer "ACME lab, bench 3"', "own note at the bottom right"),
                ("record -t 30 --csv --png", "both, timestamped names"),
                ("record -t 2 --csv fast.csv --raw", "every sample (50 kHz)"),
                ("record --csv slow.csv --rate 10", "10 averaged rows per second"),
                ("record -t 1 --power-on --png boot.png", "power up the board and record its boot"),
                ("record -t 0.2 --power-on --pre 0.01 --csv --raw", "first 190 ms, every sample"),
                ("record -t 10 --power-on --png boot.png --serial /dev/ttyUSB0",
                 "power-up with the boot log at 115200 Bd"),
                ("record -t 5 --png --serial /dev/ttyACM1 --baud 921600",
                 "console at 921600 Bd"),
            ), configure=_configure_record, duration=lambda a: a.time,
            progress_label="recording"),
    Command("output", "switch the main output on or off",
            "Powers or unpowers the device under test and confirms the new state by reading "
            "it back.",
            run_output, examples=(("output on", "power the device under test"),
                                  ("output off", "unpower it")),
            configure=_configure_output),
    Command("power-cycle", "main output off, wait, on (ends ON)",
            "Switches the main output off, waits --off seconds, and switches it on. The output "
            "is on afterwards even if it was off before.",
            run_power_cycle, examples=(("power-cycle", "1 s off"),
                                       ("power-cycle -o 5", "5 s off, e.g. to drain capacitors")),
            configure=_configure_power_cycle, duration=lambda a: a.off,
            progress_label="output off"),
    Command("safe", "all outputs off, set voltage setpoint and current limit",
            "Switches the main and expansion-port outputs off, then sets the voltage setpoint "
            "and current limit (defaults 5 V, 500 mA) and verifies them by reading back.",
            run_safe, examples=(("safe", "outputs off, 5 V, 500 mA"),
                                ("safe --voltage 3.3 --current-limit 0.1", "3.3 V, 100 mA")),
            configure=_configure_safe),
)

COMMANDS_BY_NAME: dict[str, Command] = {c.name: c for c in COMMANDS}


def add_command_parsers(subparsers: argparse._SubParsersAction, *,
                        example_prefix: str) -> dict[str, argparse.ArgumentParser]:
    """Add one subcommand per catalogue entry; returns their parsers by name."""
    parsers = {}
    for command in COMMANDS:
        parser = subparsers.add_parser(
            command.name, prog=f"{example_prefix}{command.name}", help=command.help,
            description=command.description,
            epilog=command.epilog(example_prefix),
            formatter_class=argparse.RawDescriptionHelpFormatter)
        command.configure(parser)
        parser.set_defaults(command=command)
        parsers[command.name] = parser
    return parsers


def completion_words(parser: argparse.ArgumentParser) -> list[tuple[str, bool]]:
    """Options and fixed choices of a command, each with whether it takes a file or port name.

    argparse has no public API to list its options; this is the one place that reads
    ``_actions``.
    """
    words: list[tuple[str, bool]] = []
    for action in parser._actions:
        words += [(option, action.metavar in ("FILE", "PORT"))
                  for option in action.option_strings]
        if not action.option_strings and action.choices:
            words += [(choice, False) for choice in action.choices]
    return words
