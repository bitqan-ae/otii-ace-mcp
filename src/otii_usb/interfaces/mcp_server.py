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
# Tools: status, measure, record (CSV and/or PNG into the output directory),
# set_output and power_cycle (REQ-UI-MCP-001); power_cycle always ends with
# the output on and says so (REQ-SAFE-OUT-008). Reads are annotated read-only,
# output switching destructive, so clients ask before switching power
# (REQ-SAFE-MCP-001); numeric parameters are clamped (REQ-SAFE-DEV-004) and
# file names are confined to the output directory (REQ-SAFE-MCP-002).
#
# A background monitor polls the idle device for overcurrent and for an
# output switched off by the device; its warnings are logged to stderr and
# attached as "warnings" to the next tool result, so the client sees them
# (REQ-SAFE-MON-001).
#
# Lifecycle: at start-up the device gets the safe state - main and expansion
# outputs off, 5 V, 500 mA - before any tool runs (REQ-SAFE-OUT-001). An
# unsupported device stops the server with exit code 2 (REQ-SAFE-DEV-001). On
# client disconnect, Ctrl-C, SIGTERM or SIGHUP a dedicated thread cancels a
# running tool, restores the safe state and exits; this does not depend on the
# event loop, which may be blocked reading stdin (REQ-SAFE-OUT-002,
# REQ-SAFE-OUT-004).

"""MCP server (stdio): ``otii-usb-mcp``."""
import argparse
import logging
import math
import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..device.models import SafeDefaults
from ..device.otii import OtiiDevice
from ..errors import OtiiError, UnsupportedDeviceError
from ..measurement.stats import MeasurementSummary
from .gateway import DeviceGateway
from .lifecycle import SafeExit
from .monitor import EventQueue, OvercurrentMonitor

log = logging.getLogger(__name__)
EXIT_UNSUPPORTED = 2

INSTRUCTIONS = ("Controls a Qoitech Otii Ace Pro power analyzer over USB: read main output "
                "voltage/current/power, record them to CSV files and PNG plots, switch the main "
                "output, power-cycle the device under test. All values are SI units.")

type ToolAction = Callable[[OtiiDevice], tuple[dict, MeasurementSummary | None]]


class OutputDirectory:
    """Confines files written on behalf of MCP clients to one directory (REQ-SAFE-MCP-002)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def stem(self, name: str | None) -> Path:
        """``root/<name>`` without suffix; ``name`` must be a plain file name."""
        name = name or datetime.now().strftime("otii-%Y%m%d-%H%M%S")
        if Path(name).name != name or name in (".", "..") or name.startswith("."):
            raise ValueError("name must be a plain file name (no directories)")
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / name


def clamp(value: float, low: float, high: float, name: str) -> float:
    """Limit a tool parameter to [low, high]; NaN and infinities are refused (REQ-SAFE-DEV-004)."""
    if not math.isfinite(value):
        raise RuntimeError(f"{name} must be a finite number")
    return min(max(value, low), high)


def create_server(gateway: DeviceGateway, output_dir: OutputDirectory | None = None,
                  events: EventQueue | None = None,
                  monitor: OvercurrentMonitor | None = None) -> MCPServer:
    server = MCPServer(name="otii", instructions=INSTRUCTIONS)
    outputs = output_dir or OutputDirectory("otii-records")

    async def run(action: ToolAction) -> dict:
        def call(device: OtiiDevice) -> dict:
            result, summary = action(device)
            if monitor is not None and summary is not None:
                monitor.check_summary(summary, device.current_limit)
            return result
        try:
            # abandon_on_cancel: a cancelled request returns at once; the worker thread is
            # stopped through the gateway's cancel event when the server shuts down.
            result = await anyio.to_thread.run_sync(gateway.call, call, abandon_on_cancel=True)
        except (OtiiError, ValueError, OSError) as e:
            raise RuntimeError(str(e)) from None
        warnings = events.drain() if events is not None else []
        return {**result, "warnings": warnings} if warnings else result

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True))
    async def status() -> dict:
        """Device model, serial numbers, firmware, protocol version, main output state,
        voltage setpoint and current limit."""
        return await run(lambda d: (d.info().to_dict(), None))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True))
    async def measure(duration_s: float = 0.5) -> dict:
        """Record main voltage [V], current [A] and power [W] for duration_s seconds
        (0.01..10 s) and return avg/min/max/std per channel plus the output state."""
        duration = clamp(duration_s, 0.01, 10.0, "duration_s")

        def action(d: OtiiDevice) -> tuple[dict, MeasurementSummary]:
            output = "on" if d.output_enabled else "off"
            summary = d.measure(duration)
            return {"output": output, **summary.to_dict()}, summary
        return await run(action)

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
        duration = clamp(duration_s, 0.01, 600.0, "duration_s")
        rate = None if rate_hz is None else clamp(rate_hz, 1.0, 50_000.0, "rate_hz")
        try:
            stem = outputs.stem(name)
        except (ValueError, OSError) as e:
            raise RuntimeError(str(e)) from None

        def action(d: OtiiDevice) -> tuple[dict, MeasurementSummary]:
            result = d.capture(duration, csv=stem.with_suffix(".csv") if csv else None,
                               png=stem.with_suffix(".png") if png else None, row_rate=rate)
            return result.to_dict(), result.summary
        return await run(action)

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True))
    async def set_output(on: bool) -> dict:
        """Switch the main power output on or off (powers/unpowers the device under test)."""
        return await run(lambda d: (d.set_output(on).to_dict(), None))

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    async def power_cycle(off_s: float = 1.0) -> dict:
        """Main output off, wait off_s seconds (0.05..60), on. Always ends with the output ON."""
        off = clamp(off_s, 0.05, 60.0, "off_s")
        return await run(lambda d: (d.power_cycle(off).to_dict(), None))

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
    parser.add_argument("--poll", type=float, default=1.0, metavar="SECONDS",
                        help="overcurrent monitor interval, 0 = off (default 1 s)")
    parser.add_argument("--debug", action="store_true",
                        help="log all protocol traffic to stderr")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the server; never returns (the process exits after the safe shutdown)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="otii-usb-mcp: %(message)s")
    if args.debug:
        logging.getLogger("otii_usb.protocol").setLevel(logging.DEBUG)
    try:
        defaults = SafeDefaults(voltage=args.voltage, current_limit=args.current_limit)
    except ValueError as e:
        parser.error(str(e))

    gateway = DeviceGateway.create(args.port, simulate=args.simulate, defaults=defaults)
    events = EventQueue()
    monitor = (OvercurrentMonitor(gateway, events.push, interval=args.poll)
               if args.poll > 0 else None)

    def shutdown() -> object:
        if monitor is not None:
            monitor.stop(wait=False)       # a running poll is cancelled by the gateway
        return gateway.shutdown()
    safe_exit = SafeExit(shutdown)
    safe_exit.install()
    try:
        try:
            gateway.ensure_safe()
            log.info("device: %s", gateway.call(lambda d: d.info().identity))
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
            if monitor is not None:
                monitor.start()
            create_server(gateway, OutputDirectory(args.output_dir), events, monitor).run("stdio")
    finally:
        log.info("shutting down: switching outputs off")
        safe_exit.exit_now()


if __name__ == "__main__":
    main()
