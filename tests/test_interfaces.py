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
# JSON output of the commands, usage and device errors with their exit
# codes, help with examples for every command (SW-014), the gateway's safe
# state before the first tool (SAF-001) and the list of MCP tools
# (SW-016).

import json

import pytest

from otii_usb.device.models import SafeDefaults
from otii_usb.interfaces.cli import main
from otii_usb.interfaces.common import device_factory, format_si


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
        pytest.importorskip("mcp")
        from otii_usb.interfaces.gateway import DeviceGateway
        return DeviceGateway(device_factory(simulate=True), SafeDefaults())

    def test_safe_state_applied_before_first_tool(self, gateway):
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
