# test_power_on.py: tests for recordings that switch the output on
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
# Power-on recordings (REQ-MEAS-REC-004) against the simulator: the time the
# switch-on command was sent on the recording's time axis, the output_on CSV
# column, the plot, the output off at the end and after errors, the refusal
# with the output already on, and the CLI and MCP options. Also
# Session.pump_until on its own.

import json

import numpy as np
import pytest

from otii_usb.device.models import PowerOnEvent
from otii_usb.interfaces.cli import main
from otii_usb.measurement import CsvSink
from otii_usb.protocol.session import Session

from .conftest import SilentTransport


def raw_csv(device, tmp_path, **kwargs):
    result = device.capture(kwargs.pop("duration", 0.2), csv=tmp_path / "on.csv", row_rate=None,
                            power_on=True, **kwargs)
    return result, np.loadtxt(result.csv_path, delimiter=",", skiprows=1)


def test_command_time_marked_in_csv(device, simulator, tmp_path):
    result, rows = raw_csv(device, tmp_path, pre_trigger=0.03)
    header = result.csv_path.read_text().splitlines()[0]
    assert header == "time_s,voltage_v,current_a,power_w,output_on"
    sent = result.power_on.command_sent
    assert 0.03 <= sent < 0.2
    flags = rows[:, 4]
    first_on = int(np.argmax(flags == 1))
    assert rows[first_on, 0] == pytest.approx(sent)
    assert set(flags[:first_on]) == {0} and set(flags[first_on:]) == {1}
    np.testing.assert_allclose(rows[:first_on, 1], simulator.config.off_voltage, rtol=1e-6)
    assert rows[-1, 1] == pytest.approx(3.0)              # the output did switch on


def test_switch_on_is_written_before_it_is_read_back(device, simulator, tmp_path):
    from otii_usb.device.properties import Properties
    from otii_usb.protocol.constants import Command
    simulator.requests.clear()
    raw_csv(device, tmp_path)
    started = [i for i, r in enumerate(simulator.requests) if r.command == Command.RECORD_START][0]
    first = simulator.requests[started + 1:][0]
    assert first.command == Command.SET_PROPERTY
    assert first.args == Properties.MAIN_OUTPUT.wire_id.to_bytes(4, "little") + b"\1\0\0\0"


def test_output_off_after_the_recording(device, simulator, tmp_path):
    result, _ = raw_csv(device, tmp_path)
    assert simulator.state.output_enabled is False
    assert not simulator.state.recording and not simulator.state.enabled_channels
    assert result.to_dict()["power_on"]["output_after"] == "off"
    assert result.power_on.on_at_end and result.power_on.warning is None


def test_averaged_rows_mark_the_bin_with_the_command(device, tmp_path):
    result = device.capture(0.2, csv=tmp_path / "avg.csv", row_rate=1000, power_on=True)
    rows = np.loadtxt(result.csv_path, delimiter=",", skiprows=1)
    sent = result.power_on.command_sent
    first_on = int(np.argmax(rows[:, 4] == 1))
    assert rows[first_on, 0] <= sent < rows[first_on, 0] + 1e-3


def test_plot_rendered(device, tmp_path):
    result = device.capture(0.1, png=tmp_path / "on.png", power_on=True, pre_trigger=0.02)
    assert result.png_path.stat().st_size > 0 and result.power_on.command_sent >= 0.02


def test_refused_with_output_on(device, simulator, tmp_path):
    device.set_output(True)
    simulator.requests.clear()
    with pytest.raises(ValueError, match="already on"):
        device.capture(0.1, csv=tmp_path / "x.csv", power_on=True)
    assert simulator.state.output_enabled is True and not simulator.state.recording
    assert not (tmp_path / "x.csv").exists()


@pytest.mark.parametrize("pre", [-0.01, 0.1, 0.5])
def test_pre_trigger_must_fit_the_duration(device, tmp_path, pre):
    with pytest.raises(ValueError, match="pre-trigger"):
        device.capture(0.1, csv=tmp_path / "x.csv", power_on=True, pre_trigger=pre)


def test_interrupted_recording_switches_off(device, simulator, tmp_path, monkeypatch):
    original = CsvSink.write

    def interrupt_after_switch_on(self, sequence, samples):
        if simulator.state.output_enabled and sequence > 60:
            raise KeyboardInterrupt
        original(self, sequence, samples)
    monkeypatch.setattr(CsvSink, "write", interrupt_after_switch_on)
    with pytest.raises(KeyboardInterrupt):
        device.capture(5.0, csv=tmp_path / "x.csv", power_on=True)
    assert simulator.state.output_enabled is False and not simulator.state.recording


def test_device_switching_off_is_reported(device, simulator, tmp_path, monkeypatch):
    original = CsvSink.write

    def trip(self, sequence, samples):
        if simulator.state.output_enabled and sequence > 120:
            simulator.state.output_enabled = False          # as the device's protection would
        original(self, sequence, samples)
    monkeypatch.setattr(CsvSink, "write", trip)
    result, _ = raw_csv(device, tmp_path)
    assert result.power_on.on_at_end is False
    assert "switched the output off" in result.power_on.warning


def test_power_on_event_dict():
    assert PowerOnEvent(0.05, True).to_dict() == {
        "command_sent_s": 0.05, "output_at_end": "on", "output_after": "off"}


def test_pump_until_times_out():
    session = Session(SilentTransport())
    assert session.pump_until(lambda: False, 0.06) is False
    assert session.pump_until(lambda: True, 0.06) is True


def test_cli_power_on(tmp_path, capsys):
    path = tmp_path / "cli.csv"
    assert main(["--simulate", "--json", "record", "--time", "0.1", "--csv", str(path),
                 "--power-on", "--pre", "0.02"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["power_on"]["command_sent_s"] >= 0.02 and out["power_on"]["output_after"] == "off"
    assert path.read_text().splitlines()[0].endswith(",output_on")


def test_cli_rejects_negative_pre(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--simulate", "record", "--power-on", "--pre", "-1"])
    assert exit_info.value.code == 2


def test_mcp_record_power_on(tmp_path):
    import anyio
    from otii_usb.device.models import SafeDefaults
    from otii_usb.interfaces.common import device_factory
    from otii_usb.interfaces.gateway import DeviceGateway
    from otii_usb.interfaces.mcp_server import OutputDirectory, create_server
    outputs = OutputDirectory(tmp_path / "records")
    server = create_server(DeviceGateway(device_factory(simulate=True), SafeDefaults()), outputs)

    async def call():
        return await server.call_tool("record", {"duration_s": 0.1, "name": "boot",
                                                 "png": False, "power_on": True,
                                                 "pre_trigger_s": 0.02})
    data = json.loads(anyio.run(call).content[0].text)
    assert data["power_on"]["command_sent_s"] >= 0.02
    assert (outputs.root / "boot.csv").read_text().splitlines()[0].endswith(",output_on")
