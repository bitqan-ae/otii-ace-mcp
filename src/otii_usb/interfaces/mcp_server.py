#!/usr/bin/env python3
#
# mcp_server.py: the otii-usb-mcp server - the Otii Ace Pro as MCP tools
# over stdio
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
# Tools: status, measure, record (CSV and/or PNG into the output
# directory), set_output and power_cycle. Reads are annotated read-only,
# output switching destructive, so clients ask before switching power
# (SAF-011); numeric parameters are clamped (SAF-007) and file names are
# confined to the output directory (SAF-015).
#
# Lifecycle: at start-up the device gets the safe state - main and
# expansion outputs off, 5 V, 500 mA - before any tool runs (SAF-001). An
# unsupported device stops the server with exit code 2 (SAF-008). On client
# disconnect, Ctrl-C, SIGTERM or SIGHUP a dedicated thread cancels a
# running tool, restores the safe state and exits; this does not depend on
# the event loop, which may be blocked reading stdin (SAF-002, SAF-004).

"""MCP server (stdio): ``otii-usb-mcp``.

On startup the device is put into a safe state: main and expansion-port outputs off, voltage
setpoint 5 V, current limit 500 mA (configurable). If no device is attached at startup, the
safe state is applied on the first successful connection instead, before any tool runs.

On shutdown (client disconnect, Ctrl-C, SIGTERM, SIGHUP) a running tool is cancelled and the
same safe state is restored: recording stopped, all outputs off, startup defaults.
"""
import argparse
import logging
import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from ..device.models import SafeDefaults
from ..device.otii import OtiiDevice
from ..errors import OtiiError, UnsupportedDeviceError
from .gateway import DeviceGateway
from .lifecycle import SafeExit

log = logging.getLogger(__name__)
EXIT_UNSUPPORTED = 2

INSTRUCTIONS = ("Controls a Qoitech Otii Ace Pro power analyzer over USB: read main output "
                "voltage/current/power, record them to CSV files and PNG plots, switch the main "
                "output, power-cycle the device under test. All values are SI units.")


class OutputDirectory:
    """Confines files written on behalf of MCP clients to one directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def stem(self, name: str | None) -> Path:
        """``root/<name>`` without suffix; ``name`` must be a plain file name."""
        name = name or datetime.now().strftime("otii-%Y%m%d-%H%M%S")
        if Path(name).name != name or name in (".", "..") or name.startswith("."):
            raise ValueError("name must be a plain file name (no directories)")
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / name


async def _run[T](gateway: DeviceGateway, action: Callable[[OtiiDevice], T]) -> T:
    try:
        # abandon_on_cancel: a cancelled request returns at once; the worker thread is stopped
        # through the gateway's cancel event when the server shuts down.
        return await anyio.to_thread.run_sync(gateway.call, action, abandon_on_cancel=True)
    except (OtiiError, ValueError) as e:
        raise RuntimeError(str(e)) from None


def create_server(gateway: DeviceGateway, output_dir: OutputDirectory | None = None) -> MCPServer:
    server = MCPServer(name="otii", instructions=INSTRUCTIONS)
    outputs = output_dir or OutputDirectory("otii-records")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True))
    async def status() -> dict:
        """Device model, serial number, firmware, main output state, voltage setpoint and
        current limit."""
        return await _run(gateway, lambda d: d.info().to_dict())

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True))
    async def measure(duration_s: float = 0.5) -> dict:
        """Record main voltage [V], current [A] and power [W] for duration_s seconds
        (0.01..10 s) and return avg/min/max/std per channel plus the output state."""
        duration = min(max(duration_s, 0.01), 10.0)

        def action(d: OtiiDevice) -> dict:
            return {"output": "on" if d.output_enabled else "off", **d.measure(duration).to_dict()}
        return await _run(gateway, action)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                             idempotent_hint=False))
    async def record(duration_s: float = 10.0, csv: bool = True, png: bool = True,
                     rate_hz: float | None = 1000.0, name: str | None = None) -> dict:
        """Record main voltage [V], current [A] and power [W] for duration_s seconds
        (0.01..600) into the server's output directory: a CSV file (columns time_s, voltage_v,
        current_a, power_w; rate_hz rows per second averaged, 1..50000, null = every sample)
        and/or a PNG plot over time titled with the Otii model. Select csv and png
        individually or together (at least one). name: plain file name without suffix
        (default: timestamped). Returns the absolute paths and avg/min/max/std per channel."""
        if not (csv or png):
            raise RuntimeError("select csv and/or png")
        duration = min(max(duration_s, 0.01), 600.0)
        rate = None if rate_hz is None else min(max(rate_hz, 1.0), 50_000.0)
        try:
            stem = outputs.stem(name)
        except ValueError as e:
            raise RuntimeError(str(e)) from None
        return await _run(gateway, lambda d: d.capture(
            duration, csv=stem.with_suffix(".csv") if csv else None,
            png=stem.with_suffix(".png") if png else None, row_rate=rate).to_dict())

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True))
    async def set_output(on: bool) -> dict:
        """Switch the main power output on or off (powers/unpowers the device under test)."""
        return await _run(gateway, lambda d: d.set_output(on).to_dict())

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    async def power_cycle(off_s: float = 1.0) -> dict:
        """Main output off, wait off_s seconds (0.05..60), on. Always ends with the output ON."""
        off = min(max(off_s, 0.05), 60.0)
        return await _run(gateway, lambda d: d.power_cycle(off).to_dict())

    return server


def build_parser() -> argparse.ArgumentParser:
    defaults = SafeDefaults()
    parser = argparse.ArgumentParser(
        prog="otii-usb-mcp", description="MCP server (stdio) for the Otii Ace Pro.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""\
examples:
  otii-usb-mcp                          serve the attached Otii (auto-detected)
  otii-usb-mcp --port /dev/ttyACM1      serve a specific device
  otii-usb-mcp --voltage 3.3 --current-limit 0.1
                                        startup/shutdown defaults: 3.3 V, 100 mA
  otii-usb-mcp --output-dir ~/otii      write record() files to ~/otii
  otii-usb-mcp --simulate               serve the built-in simulator (no hardware)
  MCP clients start it over stdio: command "otii-usb-mcp", no arguments""")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--port", help="serial device (default: auto-detect)")
    target.add_argument("--simulate", action="store_true", help="use the built-in simulator")
    parser.add_argument("--voltage", type=float, default=defaults.voltage,
                        help=f"voltage setpoint at startup/shutdown (default {defaults.voltage} V)")
    parser.add_argument("--current-limit", type=float, default=defaults.current_limit,
                        help=f"current limit at startup/shutdown (default {defaults.current_limit} A)")
    parser.add_argument("--output-dir", default="otii-records",
                        help="directory for files written by the record tool")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the server; never returns (the process exits after the safe shutdown)."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="otii-usb-mcp: %(message)s")

    gateway = DeviceGateway.create(args.port, simulate=args.simulate, defaults=SafeDefaults(
        voltage=args.voltage, current_limit=args.current_limit))
    safe_exit = SafeExit(gateway.shutdown)
    safe_exit.install()
    try:
        try:
            gateway.ensure_safe()
        except UnsupportedDeviceError as e:
            log.error("%s", e)
            log.error("refusing to start; the device was not modified")
            logging.shutdown()
            os._exit(EXIT_UNSUPPORTED)
        except OtiiError as e:
            if not safe_exit.triggered:
                log.warning("device not ready at startup (%s); safe state will be applied on "
                            "first use", e)
        if not safe_exit.triggered:
            create_server(gateway, OutputDirectory(args.output_dir)).run("stdio")
    finally:
        log.info("shutting down: switching outputs off")
        safe_exit.exit_now()


if __name__ == "__main__":
    main()
