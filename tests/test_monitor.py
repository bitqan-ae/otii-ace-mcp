# test_monitor.py: the overcurrent and output-trip monitor
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
# Classification against the current limit, polled detection of
# overcurrent, near-limit currents and outputs switched off by the device,
# repetition and recovery, skipping polls while the device is busy, checks
# of finished recordings, the interactive shell's red warnings and the MCP
# server's warnings in tool results (REQ-SAFE-MON-001).

import io
import threading

import pytest

from otii_usb import OtiiDevice, SafeDefaults
from otii_usb.interfaces.console import make_console
from otii_usb.interfaces.gateway import DeviceGateway
from otii_usb.interfaces.interactive import InteractiveShell
from otii_usb.interfaces.monitor import EventQueue, OvercurrentMonitor, classify
from otii_usb.simulator import SimulatedOtii, SimulationConfig
from prompt_toolkit.history import InMemoryHistory


def setup(load_ohms: float, **config):
    sim = SimulatedOtii(SimulationConfig(sample_rate=5_000, load_resistance=load_ohms, **config))
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())   # 5 V, 0.5 A
    events = []
    monitor = OvercurrentMonitor(gateway, events.append, interval=60, window=0.01, repeat=60)
    return sim, gateway, monitor, events


def kinds(events):
    return [e.kind for e in events]


def test_classify():
    assert classify(0.5, 0.5, 0.9) == "overcurrent"
    assert classify(0.495, 0.5, 0.9) == "overcurrent"      # within 2 % counts as at the limit
    assert classify(0.46, 0.5, 0.9) == "near_limit"
    assert classify(0.2, 0.5, 0.9) is None


def test_no_event_with_output_off():
    _, _, monitor, events = setup(load_ohms=1.0)           # would be far over the limit
    assert monitor.poll_once()
    assert events == []


def test_overcurrent_reported_in_red_level():
    _, gateway, monitor, events = setup(load_ohms=5.0)     # 5 V / 5 ohm = 1 A > 0.5 A limit
    gateway.call(lambda d: d.set_output(True))
    monitor.poll_once()
    assert kinds(events) == ["overcurrent"]
    assert events[0].level == "critical" and "OVERCURRENT" in events[0].message
    assert events[0].data["current_limit_a"] == pytest.approx(0.5)


def test_near_limit_is_a_warning():
    _, gateway, monitor, events = setup(load_ohms=5.0 / 0.47)   # 0.47 A = 94 % of 0.5 A
    gateway.call(lambda d: d.set_output(True))
    monitor.poll_once()
    assert kinds(events) == ["near_limit"] and events[0].level == "warning"


def test_normal_load_is_quiet():
    _, gateway, monitor, events = setup(load_ohms=100.0)   # 50 mA
    gateway.call(lambda d: d.set_output(True))
    monitor.poll_once()
    assert events == []


def test_output_switched_off_by_device():
    sim, gateway, monitor, events = setup(load_ohms=100.0)
    gateway.call(lambda d: d.set_output(True))
    sim.state.output_enabled = False                       # the device's own protection
    monitor.poll_once()
    assert kinds(events) == ["output_tripped"] and events[0].level == "critical"


def test_trip_at_limit_simulation_reports_trip():
    sim, gateway, monitor, events = setup(load_ohms=100.0, trip_at_limit=True)
    gateway.call(lambda d: d.set_output(True))
    sim.config.load_resistance = 5.0                       # load now draws 1 A > 0.5 A limit
    monitor.poll_once()
    assert "output_tripped" in kinds(events)
    assert sim.state.output_enabled is False


def test_repeated_condition_is_not_spammed_and_recovery_reported():
    sim, gateway, monitor, events = setup(load_ohms=5.0)
    gateway.call(lambda d: d.set_output(True))
    monitor.poll_once()
    monitor.poll_once()
    assert kinds(events) == ["overcurrent"]
    sim.config.load_resistance = 100.0
    monitor.poll_once()
    assert kinds(events) == ["overcurrent", "recovered"]


def test_poll_skipped_while_device_busy():
    _, gateway, monitor, events = setup(load_ohms=5.0)
    started, release = threading.Event(), threading.Event()

    def busy(d):
        started.set()
        release.wait(5)
    worker = threading.Thread(target=lambda: gateway.call(busy))
    worker.start()
    started.wait(5)
    assert monitor.poll_once() is False
    release.set()
    worker.join()


def test_check_summary_of_a_recording(device):
    gateway = DeviceGateway(lambda: device, SafeDefaults())
    events = []
    monitor = OvercurrentMonitor(gateway, events.append)
    from otii_usb.measurement.stats import MeasurementSummary, Stats
    from otii_usb import ChannelId
    summary = MeasurementSummary(1.0, {ChannelId.MC: Stats(0.2, 0.0, 0.5, 0.1, 100)})
    monitor.check_summary(summary, 0.5)
    assert kinds(events) == ["overcurrent"] and "during the recording" in events[0].message


def test_stop_before_start_is_safe():
    _, _, monitor, _ = setup(load_ohms=100.0)
    monitor.stop()
    monitor.start()                                        # no thread after stop
    assert monitor._thread is None
    monitor.stop()


def test_event_queue_drains_once():
    queue = EventQueue(maxlen=2)
    _, gateway, monitor, _ = setup(load_ohms=5.0)
    monitor._notify = queue.push
    gateway.call(lambda d: d.set_output(True))
    monitor.poll_once()
    drained = queue.drain()
    assert drained[0]["kind"] == "overcurrent" and queue.drain() == []


def test_shell_prints_overcurrent_in_bold_red():
    sim = SimulatedOtii(SimulationConfig(sample_rate=5_000, load_resistance=5.0))
    out = io.StringIO()
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
    shell = InteractiveShell(gateway, version="t",
                             console=make_console(file=out, width=200, force_terminal=True),
                             history=InMemoryHistory(), poll_interval=60)
    shell.execute("output on")
    shell.monitor.poll_once()
    text = out.getvalue()
    assert "OVERCURRENT" in text
    assert "\x1b[1;31m" in text                          # bold red


def test_shell_checks_recording_against_limit():
    sim = SimulatedOtii(SimulationConfig(sample_rate=5_000, load_resistance=5.0))
    out = io.StringIO()
    gateway = DeviceGateway(lambda: OtiiDevice.connect(sim), SafeDefaults())
    shell = InteractiveShell(gateway, version="t", console=make_console(file=out, width=200),
                             history=InMemoryHistory(), poll_interval=60)
    shell.execute("output on")
    shell.execute("measure -t 0.02")
    assert "OVERCURRENT" in out.getvalue() and "during the recording" in out.getvalue()


def test_mcp_tool_results_carry_warnings():
    import anyio
    from otii_usb.interfaces.mcp_server import create_server
    sim, gateway, monitor, _ = setup(load_ohms=5.0)
    queue = EventQueue()
    monitor._notify = queue.push
    server = create_server(gateway, events=queue, monitor=monitor)
    gateway.call(lambda d: d.set_output(True))

    async def measure():
        return await server.call_tool("measure", {"duration_s": 0.02})
    result = anyio.run(measure)
    text = str(result.structured_content or [c.text for c in result.content])
    assert "warnings" in text and "OVERCURRENT" in text
