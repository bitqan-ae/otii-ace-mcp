# test_shutdown.py: every way of stopping must leave the outputs off
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
# Cancellation of blocking calls (SAF-004), the safe shutdown sequence and
# its retries (SAF-002, SAF-013), the gateway cancelling a running tool,
# and real signals (SIGINT, SIGTERM, SIGHUP; inherited-ignored SIGINT;
# signals after the server has been running) sent to real processes, which
# must switch the outputs off and exit within 3 s (SAF-014, SAF-017).
# Unsupported devices are refused without a single write (SAF-008).

"""Ctrl-C / SIGTERM handling: every exit path must leave the outputs off."""
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from otii_usb import OtiiDevice, SafeDefaults
from otii_usb.errors import OperationCancelledError, PortBusyError
from otii_usb.interfaces import cli
from otii_usb.interfaces.lifecycle import safe_shutdown
from otii_usb.protocol import Command, Session
from otii_usb.simulator import SimulatedOtii, SimulationConfig
from tests.conftest import SilentTransport


def bound_factory(simulator, cancel=None):
    return lambda: OtiiDevice.connect(simulator, cancel=cancel)


def test_cancel_event_aborts_blocking_request():
    cancel = threading.Event()
    session = Session(SilentTransport(), cancel=cancel)
    threading.Timer(0.1, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(OperationCancelledError):
        session.request(Command.PING, timeout=10)
    assert time.monotonic() - t0 < 1.0


def test_device_shutdown_stops_leftover_recording(simulator):
    simulator.state.output_enabled = True
    simulator.state.recording = True
    simulator.state.enabled_channels.update(simulator.channels)
    simulator.state.next_packet_at = time.monotonic()
    with OtiiDevice.connect(simulator) as dev:
        report = dev.shutdown()
    assert not simulator.state.recording
    assert not simulator.state.enabled_channels
    assert simulator.state.output_enabled is False
    assert report.voltage_setpoint == pytest.approx(5.0)


def test_safe_shutdown_retries_while_port_busy(simulator):
    calls = []

    def factory():
        calls.append(1)
        if len(calls) < 3:
            raise PortBusyError("busy")
        return OtiiDevice.connect(simulator)
    simulator.state.output_enabled = True
    assert safe_shutdown(factory, retry_delay=0.01) is not None
    assert len(calls) == 3 and simulator.state.output_enabled is False


def test_safe_shutdown_reports_failure():
    def factory():
        raise PortBusyError("busy")
    assert safe_shutdown(factory, attempts=2, retry_delay=0.01) is None


def test_cli_ctrl_c_switches_outputs_off(monkeypatch, capsys):
    sim = SimulatedOtii(SimulationConfig(sample_rate=5_000))
    monkeypatch.setattr(cli, "device_factory", lambda *a, **k: bound_factory(sim))

    def interrupted_info(dev):
        dev.set_output(True)
        raise KeyboardInterrupt
    monkeypatch.setattr(OtiiDevice, "info", interrupted_info)
    assert cli.main(["--simulate", "status"]) == cli.EXIT_INTERRUPTED
    assert sim.state.output_enabled is False
    assert sim.state.voltage_setpoint == pytest.approx(5.0)
    assert "outputs off" in capsys.readouterr().err


class TestGatewayShutdown:
    @pytest.fixture
    def setup(self):
        pytest.importorskip("mcp")
        from otii_usb.interfaces.gateway import DeviceGateway
        sim = SimulatedOtii(SimulationConfig(sample_rate=5_000))
        cancel = threading.Event()
        return sim, DeviceGateway(bound_factory(sim, cancel), SafeDefaults(), cancel)

    def test_shutdown_restores_safe_state(self, setup):
        sim, gateway = setup
        gateway.call(lambda d: d.set_output(True))
        gateway.shutdown()
        assert sim.state.output_enabled is False
        assert sim.state.current_limit == pytest.approx(0.5)

    def test_shutdown_cancels_running_tool(self, setup):
        sim, gateway = setup
        errors = []

        def long_tool():
            try:
                gateway.call(lambda d: (d.set_output(True), d.wait(30)))
            except Exception as e:      # noqa: BLE001 - recorded for the assertion
                errors.append(e)
        worker = threading.Thread(target=long_tool)
        worker.start()
        deadline = time.monotonic() + 5
        while not sim.state.output_enabled and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sim.state.output_enabled

        t0 = time.monotonic()
        gateway.shutdown()
        worker.join(timeout=5)
        assert time.monotonic() - t0 < 3
        assert sim.state.output_enabled is False
        assert isinstance(errors[0], OperationCancelledError)

    def test_no_calls_after_shutdown(self, setup):
        _, gateway = setup
        gateway.shutdown()
        with pytest.raises(RuntimeError):
            gateway.call(lambda d: d.info())


# ---------------------------------------------------------------- real signals, real processes

def _ignore_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # what a shell does for background jobs


def _spawn(module: str, *args: str, sigint_ignored: bool = False) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", module, "--simulate", *args],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            preexec_fn=_ignore_sigint if sigint_ignored else None)


def _wait_exit(proc: subprocess.Popen, timeout: float = 15.0) -> str:
    """Wait for exit *with stdin still open* (a real client keeps it open), return stderr."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("process did not exit after the signal")
    return proc.stderr.read()


def _wait_for_line(proc: subprocess.Popen, needle: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stderr.readline()
        if needle in line:
            return
        if not line and proc.poll() is not None:
            break
    pytest.fail(f"did not see {needle!r} on stderr")


SERVER_SIGNAL_CASES = [
    # (signal, SIGINT inherited as ignored, delay after startup before the signal)
    *[(sig, ignored, 0.0) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
      for ignored in (False, True)],
    # a server that has been serving for a while: the main thread is blocked in the event
    # loop and a thread is reading stdin -- the outputs must still go off at once
    *[(sig, False, 1.5) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)],
]


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
@pytest.mark.parametrize(("sig", "sigint_ignored", "delay"), SERVER_SIGNAL_CASES)
def test_mcp_server_signal_shutdown(sig, sigint_ignored, delay):
    pytest.importorskip("mcp")
    proc = _spawn("otii_usb.interfaces.mcp_server", sigint_ignored=sigint_ignored)
    _wait_for_line(proc, "safe state applied")
    time.sleep(delay)
    t0 = time.monotonic()
    proc.send_signal(sig)
    err = _wait_exit(proc)
    assert time.monotonic() - t0 < 3.0, "outputs must be switched off promptly"
    assert proc.returncode == 0
    assert "shutdown: outputs off" in err
    assert "Traceback" not in err


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_mcp_server_client_disconnect_shutdown():
    pytest.importorskip("mcp")
    proc = _spawn("otii_usb.interfaces.mcp_server")
    _wait_for_line(proc, "safe state applied")
    proc.stdin.close()
    err = _wait_exit(proc)
    assert "shutdown: outputs off" in err


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("sigint_ignored", [False, True])
def test_cli_signal_during_measure(sig, sigint_ignored):
    proc = _spawn("otii_usb", "measure", "--time", "30", sigint_ignored=sigint_ignored)
    time.sleep(1.0)
    proc.send_signal(sig)
    err = _wait_exit(proc)
    assert proc.returncode == cli.EXIT_INTERRUPTED
    assert "outputs off, defaults restored" in err


class TestUnsupportedDevice:
    @pytest.mark.parametrize("config", [
        SimulationConfig(device_type="Arc", name="Arc", firmware="3.1.3"),
        SimulationConfig(firmware="2.9.0"),
        SimulationConfig(protocol_version=4),
    ])
    def test_refused_without_writing(self, config):
        from otii_usb import UnsupportedDeviceError
        sim = SimulatedOtii(config)
        with pytest.raises(UnsupportedDeviceError):
            OtiiDevice.connect(sim)
        assert not any(r.command == Command.SET_PROPERTY for r in sim.requests)

    def test_supported_device_identified(self, device):
        assert device.model == "Otii Ace Pro"
        assert device.info().device_type == "Ace"

    def test_gateway_refuses_unsupported(self):
        pytest.importorskip("mcp")
        from otii_usb import UnsupportedDeviceError
        from otii_usb.interfaces.gateway import DeviceGateway
        sim = SimulatedOtii(SimulationConfig(device_type="Arc"))
        gateway = DeviceGateway(bound_factory(sim), SafeDefaults())
        with pytest.raises(UnsupportedDeviceError):
            gateway.ensure_safe()
        assert not any(r.command == Command.SET_PROPERTY for r in sim.requests)
