#!/usr/bin/env python3
#
# cli.py: the otii-usb command line - one command per run, or the
# interactive shell with -i
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
#   otii-usb status | measure | record | output on|off | power-cycle | safe
#   otii-usb -i                    interactive shell
#   otii-usb --json COMMAND        machine-readable output
#   otii-usb --simulate COMMAND    against the built-in simulator
#   otii-usb COMMAND --help        options and examples
#
# A one-shot command does not change the outputs by itself (so `output on`
# stays on). Ctrl-C, SIGTERM or SIGHUP stop the command, switch all outputs
# off, restore the 5 V / 500 mA defaults and exit with 130 (SAF-017).
# Exit codes: 0 ok, 1 device error, 2 usage error, 130 interrupted
# (SW-012).

"""Command line interface: ``otii-usb``."""
import argparse
import logging
import sys
from collections.abc import Sequence

from .. import __version__
from ..device.models import SafeDefaults
from ..errors import OtiiError
from .commands import add_command_parsers
from .common import device_factory
from .console import make_console, progress_bar, render
from .lifecycle import safe_shutdown, signals_shielded, termination_as_interrupt

EXIT_INTERRUPTED = 130

EPILOG = """\
examples:
  otii-usb -i                                interactive shell (completion, history)
  otii-usb status                            device identity and output state
  otii-usb measure -t 2                      2 s average/min/max of V, I, P
  otii-usb record -t 60 --csv run.csv --png run.png
                                             60 s to a CSV file and a plot
  otii-usb output on                         power the device under test
  otii-usb --json measure                    machine-readable output
  otii-usb --simulate -i                     try everything without hardware
  otii-usb COMMAND --help                    options and examples for one command

Ctrl-C (or SIGTERM) switches all outputs off and restores the 5 V / 500 mA defaults."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="otii-usb", description="Control and measure with an Otii Ace Pro over USB.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--port", help="serial device (default: auto-detect)")
    target.add_argument("--simulate", action="store_true", help="use the built-in simulator")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="interactive shell with completion, history and colours")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--debug", action="store_true", help="log all protocol traffic")
    parser.add_argument("-V", "--version", action="version", version=f"otii-usb {__version__}")
    sub = parser.add_subparsers(dest="name", metavar="COMMAND")
    add_command_parsers(sub, example_prefix="otii-usb ")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command (or the interactive shell).

    Ctrl-C / SIGTERM: switch all outputs off, restore the defaults, exit 130.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(name)s: %(message)s")
    if args.interactive:
        from .gateway import DeviceGateway
        from .interactive import run_interactive
        return run_interactive(DeviceGateway.create(args.port, simulate=args.simulate,
                                                    defaults=SafeDefaults()), __version__)
    if args.name is None:
        parser.print_help()
        return 2

    console, err = make_console(), make_console(stderr=True)
    command = args.command
    factory = device_factory(args.port, simulate=args.simulate)
    try:
        with termination_as_interrupt():
            with factory() as dev, progress_bar(err, command.progress_label or command.name,
                                                command.duration(args)) as progress:
                result = command.run(dev, args, progress)
        render(console, result, as_json=args.json)
    except KeyboardInterrupt:
        with signals_shielded():
            err.print("[warn]interrupted: switching outputs off ...[/]")
            report = safe_shutdown(factory)
            err.print("outputs off, defaults restored" if report else
                      "[error]error: could not reach the device to switch outputs off[/]")
        return EXIT_INTERRUPTED
    except (OtiiError, ValueError, PermissionError) as e:
        err.print(f"[error]error:[/] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
