# test_interfaces.py: the command line and the MCP server
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
# JSON output of the commands, usage and device errors with their exit codes,
# help with examples for every command (REQ-UI-CLI-002), the gateway's safe
# state before the first tool (REQ-SAFE-OUT-001) and the list of MCP tools
# (REQ-UI-MCP-001).

import json

import pytest

from otii_usb import OtiiDevice
from otii_usb.device.models import SafeDefaults
from otii_usb.simulator import SimulatedOtii, SimulationConfig
from otii_usb.interfaces.cli import main
from otii_usb.interfaces.common import device_factory, format_si
from otii_usb.interfaces.gateway import DeviceGateway


def run_cli(capsys, *argv) -> dict:
    assert main(["--simulate", "--json", *argv]) == 0
    return json.loads(capsys.readouterr().out)


class TestCli:
    def test_status(self, capsys):
        assert run_cli(capsys, "status")["name"] == "Ace"

    def test_measure(self, capsys):
        out = run_cli(capsys, "measure", "--time", "0.02")
        assert out["voltage_v"]["avg"] == pytest.approx(0.0045)
        assert {"current_a", "power_w"} <= out.keys()

    def test_safe(self, capsys):
        out = run_cli(capsys, "safe", "--voltage", "4", "--current-limit", "0.2")
        assert out["voltage_setpoint_v"] == pytest.approx(4.0)
        assert out["current_limit_a"] == pytest.approx(0.2)

    def test_invalid_argument_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--simulate", "measure", "--time", "-1"])
        assert exc.value.code == 2
        assert "must be positive" in capsys.readouterr().err

    def test_device_error_exit_code(self, capsys):
        assert main(["--port", "/dev/does-not-exist", "status"]) == 1
        assert "error" in capsys.readouterr().err

    def test_every_command_has_help_with_examples(self, capsys):
        from otii_usb.interfaces.commands import COMMANDS
        for command in COMMANDS:
            with pytest.raises(SystemExit):
                main([command.name, "--help"])
            text = capsys.readouterr().out
            assert command.description.split(".")[0] in text.replace("\n", " ")
            assert "examples:" in text and f"otii-usb {command.name}" in text


def test_format_si():
    assert format_si(0.0123, "A") == "12.3000 mA"
    assert format_si(3.0, "V") == "3.0000 V"


class TestMcpGateway:
    @pytest.fixture
    def gateway(self):
        from otii_usb.interfaces.gateway import DeviceGateway
        return DeviceGateway(device_factory(simulate=True), SafeDefaults())

    def test_safe_state_applied_before_first_tool(self):
        sim = SimulatedOtii(SimulationConfig(output_enabled=True, voltage_setpoint=12.0))
        gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
        info = gateway.call(lambda d: d.info())
        assert info.output_enabled is False
        assert info.voltage_setpoint == pytest.approx(5.0)
        assert info.current_limit == pytest.approx(0.5)

    def test_safe_state_applied_only_once(self, gateway):
        gateway.ensure_safe()
        gateway.call(lambda d: d.set_output(True))
        assert gateway.call(lambda d: d.output_enabled) is True

    def test_server_lists_tools(self, gateway):
        import anyio
        from otii_usb.interfaces.mcp_server import create_server
        server = create_server(gateway)

        async def names():
            return sorted(t.name for t in await server.list_tools())
        assert anyio.run(names) == ["measure", "power_cycle", "record", "set_output", "status"]


class TestReviewRegressions:
    def test_unsupported_device_exit_code_2(self, monkeypatch, capsys):
        from otii_usb.interfaces import cli
        sim = SimulatedOtii(SimulationConfig(device_type="Arc"))
        monkeypatch.setattr(cli, "device_factory", lambda *a, **k: lambda: OtiiDevice.connect(sim))
        assert main(["--json", "status"]) == 2
        assert "unsupported device" in json.loads(capsys.readouterr().out)["error"]

    def test_file_error_is_reported_not_raised(self, capsys, tmp_path):
        missing = tmp_path / "missing-dir" / "x.csv"
        assert main(["--simulate", "--json", "record", "-t", "0.02", "--csv", str(missing)]) == 1
        assert "error" in json.loads(capsys.readouterr().out)

    def test_gateway_with_cancel_needs_safe_factory(self):
        import threading
        with pytest.raises(ValueError):
            DeviceGateway(lambda: None, SafeDefaults(), threading.Event())

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_mcp_clamp_refuses_non_finite(self, value):
        from otii_usb.interfaces.mcp_server import clamp
        with pytest.raises(RuntimeError):
            clamp(value, 0.05, 60.0, "off_s")

    def test_mcp_clamp_limits(self):
        from otii_usb.interfaces.mcp_server import clamp
        assert clamp(1000.0, 0.05, 60.0, "off_s") == 60.0
        assert clamp(-1.0, 0.05, 60.0, "off_s") == 0.05

    def test_mcp_tool_annotations(self):
        """Reads are read-only, output switching is destructive (REQ-SAFE-MCP-001)."""
        import anyio
        from otii_usb.interfaces.mcp_server import create_server
        server = create_server(DeviceGateway(device_factory(simulate=True), SafeDefaults()))

        async def tools():
            return {t.name: t.annotations for t in await server.list_tools()}
        annotations = anyio.run(tools)
        assert annotations["status"].read_only_hint and annotations["measure"].read_only_hint
        assert annotations["set_output"].destructive_hint
        assert annotations["power_cycle"].destructive_hint
        assert not annotations["record"].read_only_hint
        assert annotations["record"].destructive_hint           # power_on switches the output

    def test_mcp_power_cycle_rejects_nan(self):
        import anyio
        from otii_usb.interfaces.mcp_server import create_server
        sim = SimulatedOtii()
        server = create_server(DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults()))

        async def call():
            return await server.call_tool("power_cycle", {"off_s": float("nan")})
        with pytest.raises(Exception) as error:
            anyio.run(call)
        assert "finite" in str(error.value.__cause__)
        assert not any(r.command == 0x66 and r.args[:4] == b"\x09\0\0\0" and r.args[4:] == b"\1\0\0\0"
                       for r in sim.requests)                # the output was never switched on

    def test_progress_bar_on_terminal_only(self):
        import io
        from otii_usb.interfaces.console import make_console, progress_bar
        out = io.StringIO()
        with progress_bar(make_console(file=out, force_terminal=True), "measuring", 1.0) as cb:
            assert cb is not None
            cb(0.5, 1.0)
        assert "measuring" in out.getvalue()
        with progress_bar(make_console(file=io.StringIO()), "measuring", 1.0) as cb:
            assert cb is None


def test_port_is_exclusive():
    """A second open of the same serial port is refused (REQ-SAFE-DEV-006)."""
    import os
    from otii_usb.errors import PortBusyError
    from otii_usb.transport import SerialTransport
    master, slave = os.openpty()
    try:
        path = os.ttyname(slave)
        with SerialTransport(path):
            with pytest.raises(PortBusyError):
                SerialTransport(path)
    finally:
        os.close(master)
        os.close(slave)
