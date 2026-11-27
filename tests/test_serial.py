# test_serial.py: tests for the serial terminal panel of a recording
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
# The serial console of the device under test in a recording
# (REQ-MEAS-REC-009): line splitting and timing, the host-to-recording
# clock, the reader on a pseudo-terminal (read-only, exclusive), the fourth
# plot panel without overlapping labels, and the CLI and MCP options.

import json
import os
import select
import time
from datetime import datetime

import numpy as np
import pytest
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from otii_usb.device import OtiiDevice
from otii_usb.errors import PortBusyError, TransportError
from otii_usb.interfaces.cli import main
from otii_usb.measurement.serial_text import (HostClock, SerialLine, SerialText,
                                              character_rate, character_times, clean,
                                              split_lines)
from otii_usb.measurement.stats import MeasurementSummary
from otii_usb.measurement.binning import Bins
from otii_usb.report.plot import PlotInfo, place, render_png, spaced
from otii_usb.transport.uart import Chunk, UartReader

CHAR = 10 / 115200


def same(t: float) -> float:
    return t


@pytest.fixture
def pty():
    """A pseudo-terminal: (master fd, slave path); the slave stays open so it keeps working."""
    master, slave = os.openpty()
    yield master, os.ttyname(slave)
    os.close(master)
    os.close(slave)


@pytest.fixture
def figures(monkeypatch) -> list[Figure]:
    saved = []
    original = Figure.savefig

    def keep(self, *args, **kwargs):
        saved.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", keep)
    return saved


# ---------------------------------------------------------------- lines and timing

OPEN = [0.0]                            # when the port was opened (host time)


def test_line_timed_at_its_first_printable_byte():
    lines = split_lines([Chunk(1.0, b"\r\nab\r\n")], CHAR, same, OPEN)
    assert [line.text for line in lines] == ["ab"]
    assert lines[0].time == pytest.approx(1.0 - 3 * CHAR)        # b \r \n after it


def test_back_dating_stops_at_the_previous_read():
    chunks = [Chunk(1.0, b"x\n"), Chunk(1.0001, b"y" * 100 + b"\n")]
    assert split_lines(chunks, CHAR, same, OPEN)[1].time == 1.0


def test_back_dating_stops_at_the_opening():
    assert split_lines([Chunk(1.0, b"y" * 1000 + b"\n")], CHAR, same, [0.99])[0].time == 0.99


def test_line_split_over_chunks_keeps_the_first_time():
    chunks = [Chunk(1.0, b"hel"), Chunk(2.0, b"lo\nnext")]
    lines = split_lines(chunks, CHAR, same, OPEN)
    assert [(line.time, line.text) for line in lines] == [
        (pytest.approx(1.0 - 2 * CHAR), "hello"), (pytest.approx(2.0 - 3 * CHAR), "next")]


def test_first_text_after_opening_starts_a_line():
    lines = split_lines([Chunk(1.0, b"tail\nfull\n")], CHAR, same, [0.9999])
    assert [line.text for line in lines] == ["tail", "full"]


def test_reopened_port_starts_a_new_stream():
    chunks = [Chunk(1.0, b"one\ntwo, cut", 0), Chunk(3.0, b"after\nthree\n", 1)]
    lines = split_lines(chunks, CHAR, same, [0.0, 2.0])
    assert [(line.time, line.text) for line in lines] == [
        (pytest.approx(1.0 - 11 * CHAR), "one"), (pytest.approx(1.0 - 7 * CHAR), "two, cut"),
        (pytest.approx(3.0 - 11 * CHAR), "after"), (pytest.approx(3.0 - 5 * CHAR), "three")]


def test_times_mapped_to_the_recording():
    lines = split_lines([Chunk(10.0, b"a\n")], CHAR, lambda t: t - 9.0, OPEN)
    assert lines[0].time == pytest.approx(1.0 - CHAR)


@pytest.mark.parametrize(("raw", "text"), [
    (b"\x1b[1;32mOK\x1b[0m done", "OK done"),
    (b"10%\r20%\r", "20%"),
    (b"a\tb\x07\x00", "a    b"),
    (b"caf\xc3\xa9 \xff", "café �"),
    (b"  \r", ""),
])
def test_clean(raw, text):
    assert clean(raw) == text


def test_blank_lines_are_dropped():
    lines = split_lines([Chunk(1.0, b"a\r\n\r\n   \r\n\x1b[0m\r\nb\r\n")], CHAR, same, OPEN)
    assert [line.text for line in lines] == ["a", "b"]


def test_character_times_dated_back_like_the_lines():
    chunks = [Chunk(1.0, b"ab\r\n", 0), Chunk(1.00001, b"\x1bc\xc3\xa9" + b"d" * 200, 0),
              Chunk(3.0, b"e", 1)]
    times = character_times(chunks, CHAR, lambda t: t - 0.5, [0.0, 2.0])
    # a b; c é d*200 (ESC and the UTF-8 continuation byte not counted); e
    assert len(times) == 2 + 202 + 1
    assert times[:2] == pytest.approx([0.5 - 3 * CHAR, 0.5 - 2 * CHAR])
    assert times[2] == 0.5                                  # not before the previous read
    assert times[-1] == 2.5 and np.all(np.diff(times) >= 0)


def test_character_rate_per_second():
    edges, rate = character_rate(np.array([0.01, 0.02, 0.03, 0.6, -1.0, 5.0]), 0.0, 1.0, 4)
    assert edges.tolist() == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert rate.tolist() == [12.0, 0.0, 4.0, 0.0]           # 3 and 1 characters in 0.25 s


def test_host_clock_uses_the_smallest_latency():
    now = iter([10.004, 10.003, 10.010])
    clock = HostClock(lambda: next(now))
    for sequence in (100, 101, 102):
        clock.write(sequence, {})
    # packet ends 1, 2, 3 ms; latencies 3, 1, 7 ms -> time zero at 10.001
    assert clock.offset == pytest.approx(10.001)
    assert clock.to_recording(10.5) == pytest.approx(0.499)


def test_spaced():
    assert spaced([0.0, 0.5, 1.0, 1.2, 2.2, 2.3], 1.0) == [0, 2, 4]


def test_lines_between_markers_follow_until_the_next_marker():
    times = [0.0, 0.1, 0.2, 0.3, 4.0, 4.1, 9.5]
    assert place(times, 1.0, 10.0) == [
        (0, 0.0, True), (1, 1.0, False), (2, 2.0, False), (3, 3.0, False),  # ends at 4.0
        (4, 4.0, True), (5, 5.0, False),
        (6, 9.5, True)]
    assert place([0.0, 0.1, 0.2, 0.3, 3.5], 1.0, 10.0)[:4] == [     # 0.3 would cross 3.5
        (0, 0.0, True), (1, 1.0, False), (2, 2.0, False), (4, 3.5, True)]


def test_lines_after_the_last_marker_stop_at_the_end():
    assert place([0.0, 0.1, 0.2], 1.0, 2.5) == [(0, 0.0, True), (1, 1.0, False)]


# ---------------------------------------------------------------- reader

def wait_for(condition, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.001)
    return True


def test_reader_receives_and_never_writes(pty):
    master, path = pty
    with UartReader(path, 9600) as reader:
        reader.start()
        os.write(master, b"boot\r\n")
        assert wait_for(lambda: reader._chunks)
        assert select.select([master], [], [], 0.05)[0] == []        # nothing sent back
        capture = reader.stop()
    assert b"".join(c.data for c in capture.chunks) == b"boot\r\n"
    assert len(capture.open) == 1 and capture.error is None
    assert reader.char_time == pytest.approx(10 / 9600)


def test_reader_restores_the_port_settings(pty):
    import termios
    _, path = pty
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY)
    try:
        before = termios.tcgetattr(fd)
        with UartReader(path, 9600) as reader:
            assert termios.tcgetattr(fd) != before                  # raw, 9600 Bd
            reader.start()
        assert termios.tcgetattr(fd) == before
    finally:
        os.close(fd)


def test_reader_waits_for_the_port_and_follows_it(tmp_path):
    """An adapter powered by the device under test: appears late, goes away, comes back."""
    link = tmp_path / "ttyUSB0"
    reader = UartReader(str(link))
    reader.start()
    try:
        master, slave = os.openpty()
        link.symlink_to(os.ttyname(slave))
        assert wait_for(lambda: len(reader._open) == 1)
        os.write(master, b"first\n")
        assert wait_for(lambda: reader._chunks)
        os.close(master)                                    # unplugged
        assert wait_for(lambda: reader._open[0][1] is not None)
        os.close(slave)
        master, slave = os.openpty()
        link.unlink()
        link.symlink_to(os.ttyname(slave))
        assert wait_for(lambda: len(reader._open) == 2)
        os.write(master, b"second\n")
        assert wait_for(lambda: len(reader._chunks) == 2)
    finally:
        capture = reader.stop()
    os.close(master)
    os.close(slave)
    assert [(c.data, c.connection) for c in capture.chunks] == [(b"first\n", 0),
                                                                (b"second\n", 1)]
    assert capture.open[0][0] < capture.open[0][1] < capture.open[1][0]
    assert capture.open[1][1] is not None and capture.error is None


def test_reader_reports_a_port_that_never_appeared(tmp_path):
    reader = UartReader(str(tmp_path / "ttyUSB9"))
    reader.start()
    capture = reader.stop()
    assert capture.open == () and "did not appear" in capture.error


def test_reader_reports_a_busy_port_after_a_reconnect(tmp_path):
    link = tmp_path / "ttyUSB0"
    master, slave = os.openpty()
    link.symlink_to(os.ttyname(slave))
    reader = UartReader(str(link))
    reader.start()
    try:
        os.close(master)                                    # unplugged ...
        assert wait_for(lambda: reader._open[0][1] is not None)
        os.close(slave)
        master, slave = os.openpty()
        link.unlink()
        link.symlink_to(os.ttyname(slave))
        with UartReader(str(link)):                         # ... and back, but taken
            assert wait_for(lambda: reader._error is not None)
    finally:
        capture = reader.stop()
    os.close(master)
    os.close(slave)
    assert "in use" in capture.error


def test_reader_refuses_a_port_it_may_not_open(pty, monkeypatch):
    def denied(*args):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(TransportError, match="Permission denied"):
        UartReader(pty[1])


def test_reader_port_is_exclusive(pty):
    _, path = pty
    with UartReader(path):
        with pytest.raises(PortBusyError):
            UartReader(path)


def test_reader_refuses_a_file(tmp_path):
    path = tmp_path / "file"
    path.write_text("")
    with pytest.raises(TransportError, match="not a serial port"):
        UartReader(str(path))


def test_reader_refuses_an_unknown_baud_rate(pty):
    with pytest.raises(ValueError, match="baud"):
        UartReader(pty[1], 12345)


# ---------------------------------------------------------------- capture and plot

def boot_on_switch_on(monkeypatch, master: int, text: bytes) -> None:
    """The simulated device under test prints ``text`` when it is switched on."""
    original = OtiiDevice._switch_on

    def switch_on(self):
        original(self)
        os.write(master, text)
    monkeypatch.setattr(OtiiDevice, "_switch_on", switch_on)


def test_capture_shows_lines_in_a_fourth_panel(device, pty, tmp_path, figures, monkeypatch):
    master, path = pty
    boot_on_switch_on(monkeypatch, master, b"U-Boot 2024.01\r\nStarting kernel ...\r\n")
    # a pty delivers at once; a high baud rate keeps the back-dating within microseconds
    result = device.capture(0.3, png=tmp_path / "a.png", power_on=True, pre_trigger=0.05,
                            serial_port=path, baud=4_000_000)
    assert result.serial is not None
    assert [line.text for line in result.serial.lines] == ["U-Boot 2024.01",
                                                           "Starting kernel ..."]
    assert all(line.time >= result.power_on.command_sent for line in result.serial.lines)
    data = result.to_dict()["serial"]
    assert data["bytes"] == 37 and data["lines"] == 2 and data["baud"] == 4_000_000
    assert data["open_s"][0][0] < 0.005 and data["open_s"][0][1] == 0.3
    axes = figures[0].axes
    assert [ax.get_ylabel() for ax in axes] == ["Power", "Current", "Voltage",
                                                f"Serial ({path})", "Characters/s"]
    first, second = result.serial.lines
    # one marker (plus the dashed switch-on line); the 2nd line follows in the next column
    assert [t.get_text() for t in axes[3].texts] == [
        f"{first.time:.4f} s", "U-Boot 2024.01", f"{second.time:.4f} s", "Starting kernel ..."]
    assert len(axes[3].lines) == 2
    assert figures[0].texts[-1].get_text().endswith("·   2 lines")


def test_serial_needs_png(device, pty, tmp_path):
    with pytest.raises(ValueError, match="png"):
        device.capture(0.05, csv=tmp_path / "a.csv", serial_port=pty[1])


def render_lines(tmp_path, figures, lines, duration=1.0, open_spans=((0.0, 1.0),),
                 characters=()) -> Figure:
    info = PlotInfo(model="Ace", serial_number="X", firmware="3.2.5",
                    started=datetime.now().astimezone(), duration=duration, sample_rate=1000,
                    bin_rate=1000.0)
    serial = SerialText("/dev/ttyUSB0", 115200, 0, tuple(lines), open_spans, None,
                        np.asarray(characters, dtype=np.float64))
    flat = np.ones((2, 3))
    bins = Bins(np.array([0.0, duration]), flat, flat, flat)
    render_png(tmp_path / "a.png", bins, info, MeasurementSummary(duration, {}, 0), serial)
    return figures[-1]


def test_labels_never_overlap(tmp_path, figures):
    lines = [SerialLine(i * 0.0007, f"line {i} " + "x" * (i % 90)) for i in range(1400)]
    figure = render_lines(tmp_path, figures, lines)
    ax = figure.axes[3]
    renderer = figure.canvas.get_renderer()
    stamps, texts = ax.texts[0::2], ax.texts[1::2]
    boxes = [t.get_window_extent(renderer) for t in texts]
    assert 40 < len(boxes) < 1400
    assert all(a.x1 <= b.x0 for a, b in zip(boxes, boxes[1:]))
    panel = ax.get_window_extent(renderer)
    assert all(panel.y0 <= b.y0 and b.y1 <= panel.y1 for b in boxes)       # cut to fit
    assert any(t.get_text().endswith("…") for t in texts)
    for stamp, text in zip(stamps, texts):                  # the time below its text
        assert stamp.get_window_extent(renderer).y1 <= text.get_window_extent(renderer).y0
    markers = [ax.transData.transform((line.get_xdata()[0], 0))[0] for line in ax.lines]
    assert 0 < len(markers) < len(boxes)                    # further lines without a marker
    assert not any(b.x0 < x < b.x1 for b in boxes for x in markers)   # no text over a marker


def test_arrival_time_bold_green_serial_text_black(tmp_path, figures):
    figure = render_lines(tmp_path, figures, [SerialLine(0.25, "U-Boot")])
    stamp, text = figure.axes[3].texts
    assert (stamp.get_text(), stamp.get_fontweight(), stamp.get_color()) == \
        ("0.2500 s", "bold", "#15803d")
    assert (text.get_text(), text.get_fontweight(), text.get_color()) == \
        ("U-Boot", "normal", "#000000")


def test_lines_outside_the_recording_are_not_drawn(tmp_path, figures):
    figure = render_lines(tmp_path, figures, [SerialLine(-0.1, "early"),
                                              SerialLine(0.5, "inside")])
    assert [t.get_text() for t in figure.axes[3].texts] == ["0.5000 s", "inside"]


@pytest.mark.parametrize(("open_spans", "note"), [(((0.0, 1.0),), "no serial text received"),
                                                  ((), "/dev/ttyUSB0 was not available")])
def test_panel_says_when_nothing_arrived(tmp_path, figures, open_spans, note):
    figure = render_lines(tmp_path, figures, [], open_spans=open_spans)
    assert [t.get_text() for t in figure.axes[3].texts] == [note]


def test_character_rate_behind_the_text_topped_by_its_peak(tmp_path, figures):
    characters = np.concatenate([np.full(30, 0.1), np.full(6, 0.5)])     # bins of 4 ms
    figure = render_lines(tmp_path, figures, [SerialLine(0.1, "x")], characters=characters)
    serial_ax, rate_ax = figure.axes[3], figure.axes[4]
    assert rate_ax.get_ylabel() == "Characters/s"
    assert rate_ax.get_ylim() == pytest.approx((0, 30 / 0.004))           # the peak on top
    assert rate_ax.get_yticks()[-1] == pytest.approx(30 / 0.004)
    assert serial_ax.get_zorder() > rate_ax.get_zorder()                  # text in front
    filled, edge = rate_ax.patches
    assert filled.get_alpha() < 0.2 and to_hex(filled.get_facecolor()) == "#d97706"


@pytest.mark.parametrize(("port", "label"), [
    ("/dev/ttyUSB0", "/dev/ttyUSB0"),
    ("/dev/serial/by-id/usb-wch.cn_USB_To_UART+JTAG_0123456789-if00",
     "usb-wch.cn_USB_To_UART+JTAG_0123456789-if00")])
def test_serial_axis_names_the_port(tmp_path, figures, port, label):
    info = PlotInfo(model="Ace", serial_number="X", firmware="3.2.5",
                    started=datetime.now().astimezone(), duration=1.0, sample_rate=1000,
                    bin_rate=1000.0)
    flat = np.ones((2, 3))
    render_png(tmp_path / "a.png", Bins(np.array([0.0, 1.0]), flat, flat, flat), info,
               MeasurementSummary(1.0, {}, 0), SerialText(port, 115200, 0, ()))
    assert figures[-1].axes[3].get_ylabel() == f"Serial ({label})"


def test_times_without_the_port_are_shaded(tmp_path, figures):
    figure = render_lines(tmp_path, figures, [], open_spans=((0.25, 0.5), (0.75, 1.0)))
    spans = [p.get_x() for p in figure.axes[3].patches]
    widths = [p.get_width() for p in figure.axes[3].patches]
    assert spans == [0.0, 0.5] and widths == pytest.approx([0.25, 0.25])


# ---------------------------------------------------------------- interfaces

def test_cli_serial(pty, tmp_path, capsys):
    _, path = pty
    assert main(["--simulate", "--json", "record", "--time", "0.05", "--png",
                 str(tmp_path / "a.png"), "--serial", path, "--baud", "921600"]) == 0
    out = json.loads(capsys.readouterr().out)
    serial = out.pop("serial")
    assert serial.pop("open_s")[0][1] == 0.05
    assert serial == {"port": path, "baud": 921600, "bytes": 0, "lines": 0}


def test_cli_rejects_an_unknown_baud_rate(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--simulate", "record", "--serial", "/dev/null", "--baud", "12345"])
    assert exit_info.value.code == 2


def test_mcp_record_serial(pty, tmp_path):
    import anyio
    from otii_usb.device.models import SafeDefaults
    from otii_usb.interfaces.common import device_factory
    from otii_usb.interfaces.gateway import DeviceGateway
    from otii_usb.interfaces.mcp_server import OutputDirectory, create_server
    outputs = OutputDirectory(tmp_path / "records")
    server = create_server(DeviceGateway(device_factory(simulate=True), SafeDefaults()), outputs)

    async def call(port):
        return await server.call_tool("record", {"duration_s": 0.05, "name": "boot",
                                                 "csv": False, "serial_port": port})
    data = json.loads(anyio.run(call, pty[1]).content[0].text)
    assert data["serial"]["lines"] == 0 and (outputs.root / "boot.png").exists()
    with pytest.raises(Exception) as error:
        anyio.run(call, str(tmp_path / "x"))
    assert "under /dev/" in str(error.value.__cause__)
