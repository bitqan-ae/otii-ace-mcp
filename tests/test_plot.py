# test_plot.py: tests for the PNG plot's title and panel order
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
# The default title (model and serial number) and a chosen one as plain
# text, read back from the PNG's Title, through capture, the CLI, the shell
# and the MCP record tool (REQ-MEAS-REC-005); the panels from top to bottom
# power, current, voltage (REQ-MEAS-REC-006); the footer by default, replaced
# and left out (REQ-MEAS-REC-007); local time in the plot, the file names
# and the result (REQ-MEAS-REC-008); the setpoint with the current limit
# in the details line (REQ-MEAS-REC-010).

import json
import time
from datetime import datetime, timedelta

import pytest
from matplotlib.figure import Figure
from PIL import Image

from otii_usb.interfaces.cli import main
from otii_usb.report import DEFAULT_FOOTER


def png_title(path) -> str:
    with Image.open(path) as image:
        return image.text["Title"]


def test_default_title_is_model_and_serial(device, simulator, tmp_path):
    result = device.capture(0.05, png=tmp_path / "a.png")
    assert png_title(result.png_path) == (f"{device.model}  ·  S/N "
                                          f"{simulator.config.serial_number}")


def test_title_is_plain_text(device, tmp_path):
    title = r"Boot $\nosuchcommand$ 100% ünïcode"       # mathtext would reject this
    result = device.capture(0.05, png=tmp_path / "a.png", title=title)
    assert png_title(result.png_path) == title


@pytest.fixture
def figures(monkeypatch) -> list[Figure]:
    """Every figure saved during the test."""
    saved = []
    original = Figure.savefig

    def keep(self, *args, **kwargs):
        saved.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", keep)
    return saved


def figure_texts(figure: Figure) -> list[str]:
    return [text.get_text() for text in figure.texts]


def test_details_name_setpoint_and_current_limit(device, tmp_path, figures):
    device.set_voltage_setpoint(5.0)
    device.set_current_limit(2.0)
    device.capture(0.05, png=tmp_path / "a.png")
    assert "   ·   setpoint 5 V @ 2 A   ·   " in figures[0].axes[0].get_title(loc="left")


def test_panels_power_current_voltage(device, tmp_path, figures):
    device.capture(0.05, png=tmp_path / "a.png")
    assert [ax.get_ylabel() for ax in figures[0].axes] == ["Power", "Current", "Voltage"]


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({}, DEFAULT_FOOTER), ({"footer": "ACME lab, $5"}, "ACME lab, $5"), ({"footer": None}, None)])
def test_footer(device, tmp_path, figures, kwargs, expected):
    device.capture(0.05, png=tmp_path / "a.png", **kwargs)
    notes = [t for t in figures[0].texts if t.get_text() != figures[0].get_suptitle()]
    assert [t.get_text() for t in notes] == ([expected] if expected else [])
    assert all(t.get_horizontalalignment() == "right" for t in notes)


@pytest.mark.parametrize(("options", "expected"), [
    ([], DEFAULT_FOOTER), (["--footer", " Bench 3 "], "Bench 3"), (["--no-footer"], None)])
def test_cli_footer(tmp_path, figures, options, expected):
    assert main(["--simulate", "record", "--time", "0.05", "--png", str(tmp_path / "a.png"),
                 *options]) == 0
    assert (expected in figure_texts(figures[0])) if expected else \
        figure_texts(figures[0]) == [figures[0].get_suptitle()]


def test_cli_footer_options_exclude_each_other(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--simulate", "record", "--png", str(tmp_path / "a.png"), "--footer", "x",
              "--no-footer"])
    assert exit_info.value.code == 2 and "not allowed with" in capsys.readouterr().err


@pytest.fixture
def dubai_time(monkeypatch):
    """The host's local zone set to UTC+4 (no daylight saving time) for the test."""
    monkeypatch.setenv("TZ", "Asia/Dubai")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_times_are_local(device, tmp_path, figures, dubai_time, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--simulate", "--json", "record", "--time", "0.05", "--png"]) == 0
    png = next(tmp_path.glob("otii-*.png"))
    named = datetime.strptime(png.stem, "otii-%Y%m%d-%H%M%S").astimezone()   # read as local
    assert abs(named.timestamp() - png.stat().st_mtime) < 30
    result = device.capture(0.05, png=tmp_path / "b.png")
    assert result.started.utcoffset() == timedelta(hours=4)
    assert result.to_dict()["started"].endswith("+04:00")
    subtitle = figures[-1].axes[0].get_title(loc="left")
    assert subtitle.startswith(result.started.strftime("%Y-%m-%d %H:%M:%S +04"))


def test_cli_title(tmp_path, capsys):
    path = tmp_path / "cli.png"
    assert main(["--simulate", "--json", "record", "--time", "0.05", "--png", str(path),
                 "--title", "  Sensor board, boot  "]) == 0
    assert json.loads(capsys.readouterr().out)["png"]
    assert png_title(path) == "Sensor board, boot"


def test_cli_rejects_empty_title(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--simulate", "record", "--time", "0.05", "--png", str(tmp_path / "x.png"),
              "--title", "  "])
    assert exit_info.value.code == 2 and not (tmp_path / "x.png").exists()
    assert "must not be empty" in capsys.readouterr().err


def test_mcp_record_title(tmp_path):
    import anyio
    from otii_usb.device.models import SafeDefaults
    from otii_usb.interfaces.common import device_factory
    from otii_usb.interfaces.gateway import DeviceGateway
    from otii_usb.interfaces.mcp_server import OutputDirectory, create_server
    outputs = OutputDirectory(tmp_path / "records")
    server = create_server(DeviceGateway(device_factory(simulate=True), SafeDefaults()), outputs)

    async def call(title):
        return await server.call_tool("record", {"duration_s": 0.05, "name": "t", "csv": False,
                                                 "png": True, "title": title})
    anyio.run(call, "Board 7")
    assert png_title(outputs.root / "t.png") == "Board 7"
    with pytest.raises(Exception) as error:
        anyio.run(call, "x" * 201)
    assert "200 characters" in str(error.value.__cause__)
