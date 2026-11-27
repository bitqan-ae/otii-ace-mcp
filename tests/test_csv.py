# test_csv.py: CSV recording and the MCP record tool
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
# Header, averaging to the row rate, bins across packets, raw rate, gaps,
# statistics over full-rate samples, interrupted recordings (SW-009), the
# CLI record command and the MCP output directory (SAF-015).

import io
import json

import numpy as np
import pytest

from otii_usb import ChannelId
from otii_usb.device import ChannelInfo, ChannelKind
from otii_usb.interfaces.cli import main
from otii_usb.measurement import CsvSink

RATE = 5_000
CHANNELS = {ch: ChannelInfo(ch, ChannelKind.ANALOG, True, False, 0.0, RATE)
            for ch in (ChannelId.MV, ChannelId.MC, ChannelId.MP)}


def packet(value: float, n: int = RATE // 1000) -> dict:
    return {ChannelId.MV: np.full(n, value), ChannelId.MC: np.full(n, value / 10),
            ChannelId.MP: np.full(n, value * value / 10)}


def run_sink(row_rate, packets) -> tuple[CsvSink, np.ndarray, str]:
    out = io.StringIO()
    sink = CsvSink(out, row_rate=row_rate)
    sink.start(CHANNELS)
    for sequence, samples in packets:
        sink.write(sequence, samples)
    sink.finish()
    text = out.getvalue()
    return sink, np.loadtxt(io.StringIO(text), delimiter=",", skiprows=1, ndmin=2), text


def test_header():
    _, _, text = run_sink(1000, [(0, packet(1.0))])
    assert text.splitlines()[0] == "time_s,voltage_v,current_a,power_w"


def test_averaging_to_row_rate():
    ramp = [(i, packet(float(i))) for i in range(10)]
    sink, rows, _ = run_sink(1000, ramp)
    assert sink.decimation == RATE // 1000 and sink.row_rate == 1000
    assert rows.shape == (10, 4)
    np.testing.assert_allclose(rows[:, 0], np.arange(10) * 1e-3, atol=1e-9)
    np.testing.assert_allclose(rows[:, 1], np.arange(10))


def test_bins_span_packets():
    sink, rows, _ = run_sink(400, [(i, packet(float(i % 2))) for i in range(4)])
    assert sink.decimation == round(RATE / 400)
    assert rows[0, 1] == pytest.approx(5 / 12)        # 5 zeros + 5 ones + 2 zeros


def test_raw_rate_writes_every_sample():
    sink, rows, _ = run_sink(None, [(i, packet(1.0)) for i in range(3)])
    assert sink.row_rate == RATE and rows.shape[0] == 3 * RATE // 1000
    np.testing.assert_allclose(np.diff(rows[:, 0]), 1 / RATE)


def test_gap_keeps_time_and_does_not_mix_bins():
    _, rows, _ = run_sink(1000, [(0, packet(1.0)), (1, packet(1.0)), (5, packet(9.0))])
    np.testing.assert_allclose(rows[:, 0], [0.0, 0.001, 0.005], atol=1e-9)
    assert rows[-1, 1] == 9.0


def test_stats_use_full_rate_samples():
    sink, _, _ = run_sink(1, [(0, packet(1.0)), (1, packet(3.0))])
    summary = sink.summary(0.002)
    assert summary.voltage.mean == pytest.approx(2.0)
    assert (summary.voltage.minimum, summary.voltage.maximum) == (1.0, 3.0)
    assert summary.voltage.count == 2 * RATE // 1000


def test_missing_column_rejected():
    sink = CsvSink(io.StringIO())
    with pytest.raises(ValueError):
        sink.start({ChannelId.MV: CHANNELS[ChannelId.MV]})


def test_device_capture_csv(device, tmp_path):
    device.set_output(True)
    result = device.capture(0.1, csv=tmp_path / "log.csv", row_rate=100)
    rows = np.loadtxt(result.csv_path, delimiter=",", skiprows=1)
    assert result.csv_rows == rows.shape[0] >= 8 and result.png_path is None
    np.testing.assert_allclose(rows[:, 1], 3.0)
    np.testing.assert_allclose(rows[:, 3], rows[:, 1] * rows[:, 2])
    assert result.summary.power.mean == pytest.approx(3.0 * 3.0 / 1000)


def test_interrupted_log_is_valid_csv(device, tmp_path, monkeypatch):
    calls = {"n": 0}
    original = CsvSink.write

    def interrupt_after_a_few(self, sequence, samples):
        calls["n"] += 1
        if calls["n"] > 20:
            raise KeyboardInterrupt
        original(self, sequence, samples)
    monkeypatch.setattr(CsvSink, "write", interrupt_after_a_few)
    path = tmp_path / "partial.csv"
    with pytest.raises(KeyboardInterrupt):
        device.capture(5.0, csv=path)
    rows = np.loadtxt(path, delimiter=",", skiprows=1)
    assert rows.shape == (20, 4)


def test_cli_record_csv_only(tmp_path, capsys):
    path = tmp_path / "cli.csv"
    assert main(["--simulate", "--json", "record", "--time", "0.05", "--csv", str(path),
                 "--rate", "200"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["csv"]["row_rate_hz"] == 200 and "png" not in out
    assert np.loadtxt(path, delimiter=",", skiprows=1).shape[1] == 4


class TestMcpOutputDirectory:
    @pytest.fixture
    def outputs(self, tmp_path):
        pytest.importorskip("mcp")
        from otii_usb.interfaces.mcp_server import OutputDirectory
        return OutputDirectory(tmp_path / "records")

    @pytest.mark.parametrize("name", ["../x", "/etc/x", "a/b", "..", ".hidden"])
    def test_rejects_paths(self, outputs, name):
        with pytest.raises(ValueError):
            outputs.stem(name)

    def test_default_name_inside_root(self, outputs):
        assert outputs.stem(None).parent == outputs.root

    @pytest.mark.parametrize(("csv", "png"), [(True, False), (False, True), (True, True)])
    def test_record_tool(self, outputs, csv, png):
        import anyio
        from otii_usb.device.models import SafeDefaults
        from otii_usb.interfaces.gateway import DeviceGateway
        from otii_usb.interfaces.common import device_factory
        from otii_usb.interfaces.mcp_server import create_server
        server = create_server(DeviceGateway(device_factory(simulate=True), SafeDefaults()), outputs)

        async def call():
            return await server.call_tool("record", {"duration_s": 0.05, "name": "t",
                                                     "csv": csv, "png": png})
        anyio.run(call)
        assert (outputs.root / "t.csv").exists() is csv
        assert (outputs.root / "t.png").exists() is png
