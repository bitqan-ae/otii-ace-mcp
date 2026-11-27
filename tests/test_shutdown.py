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
# Cancellation of blocking calls (REQ-SAFE-OUT-004), the safe shutdown
# sequence and its retries (REQ-SAFE-OUT-002, REQ-SAFE-DEV-005), the gateway
# cancelling a running tool, and real signals (SIGINT, SIGTERM, SIGHUP;
# inherited-ignored SIGINT; signals after the server has been running) sent to
# real processes, which must switch the outputs off and exit within 3 s
# (REQ-SAFE-OUT-007, REQ-SAFE-OUT-005). Unsupported devices are refused
# without a single write (REQ-SAFE-DEV-001).

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
        from otii_usb.interfaces.gateway import DeviceGateway
        sim = SimulatedOtii(SimulationConfig(sample_rate=5_000))
        cancel = threading.Event()
        return sim, DeviceGateway(bound_factory(sim, cancel), SafeDefaults(), cancel,
                                  safe_factory=bound_factory(sim))

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

MAIN_OUTPUT_OFF = "66 00 00 00 09 00 00 00 00 00 00 00"     # SET_PROPERTY main output = 0


def _ignore_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # what a shell does for background jobs


class Child:
    """A simulator-backed process whose stderr is read on a thread, so no pipe fills up.

    stdin stays open like a real MCP client's; every wait has a time limit.
    """

    def __init__(self, module: str, *args: str, sigint_ignored: bool = False) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", module, "--simulate", "--debug", *args],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONFAULTHANDLER": "1"},
            preexec_fn=_ignore_sigint if sigint_ignored else None)
        self._lines: list[str] = []
        self._eof = False
        self._next = 0
        self._mark = 0
        self._cond = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self.proc.stderr:
            with self._cond:
                self._lines.append(line)
                self._cond.notify_all()
        with self._cond:
            self._eof = True
            self._cond.notify_all()

    def wait_for(self, needle: str, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for i in range(self._next, len(self._lines)):
                    if needle in self._lines[i]:
                        self._next = i + 1
                        return
                self._next = len(self._lines)
                remaining = deadline - time.monotonic()
                if self._eof or remaining <= 0:
                    self.proc.kill()
                    pytest.fail(f"did not see {needle!r} on stderr")
                self._cond.wait(remaining)

    def mark(self) -> None:
        """Later output() returns only what was written after this point."""
        with self._cond:
            self._mark = len(self._lines)

    def close_stdin(self) -> None:
        self.proc.stdin.close()

    def finish(self, timeout: float = 15.0) -> str:
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.send_signal(signal.SIGABRT)       # faulthandler dumps every stack
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self._reader.join(timeout=5)
            with self._cond:
                tail = "".join(l for l in self._lines if " RX " not in l and " TX " not in l)
            pytest.fail(f"process did not exit after the signal:\n{tail[-4000:]}")
        finally:
            self._reader.join(timeout=5)
            if not self.proc.stdin.closed:
                self.proc.stdin.close()
            self.proc.stderr.close()
        with self._cond:
            return "".join(self._lines[self._mark:])


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
    child = Child("otii_usb.interfaces.mcp_server", sigint_ignored=sigint_ignored)
    child.wait_for("safe state applied")
    time.sleep(delay)          # the scenario itself: a server that has been idle for a while
    child.mark()
    t0 = time.monotonic()
    child.proc.send_signal(sig)
    err = child.finish()
    assert time.monotonic() - t0 < 3.0, "outputs must be switched off promptly"
    assert child.proc.returncode == 0
    assert MAIN_OUTPUT_OFF in err and "shutdown: outputs off" in err
    assert "Traceback" not in err


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_mcp_server_ignores_further_signals_during_shutdown():
    child = Child("otii_usb.interfaces.mcp_server")
    child.wait_for("safe state applied")
    child.mark()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGINT):
        child.proc.send_signal(sig)
    err = child.finish()
    assert child.proc.returncode == 0
    assert MAIN_OUTPUT_OFF in err and "shutdown: outputs off" in err
    assert "Traceback" not in err


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_mcp_server_client_disconnect_shutdown():
    child = Child("otii_usb.interfaces.mcp_server")
    child.wait_for("safe state applied")
    child.mark()
    child.close_stdin()
    err = child.finish()
    assert MAIN_OUTPUT_OFF in err and "shutdown: outputs off" in err


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
@pytest.mark.parametrize("sigint_ignored", [False, True])
def test_cli_signal_during_measure(sig, sigint_ignored):
    child = Child("otii_usb", "measure", "--time", "30", sigint_ignored=sigint_ignored)
    child.wait_for("RECORD_START")                       # the recording is running
    child.mark()
    child.proc.send_signal(sig)
    err = child.finish()
    assert child.proc.returncode == cli.EXIT_INTERRUPTED
    assert MAIN_OUTPUT_OFF in err and "outputs off, defaults restored" in err


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_cli_ignores_a_second_signal_during_shutdown():
    child = Child("otii_usb", "measure", "--time", "30")
    child.wait_for("RECORD_START")
    child.mark()
    child.proc.send_signal(signal.SIGINT)
    child.proc.send_signal(signal.SIGTERM)
    child.proc.send_signal(signal.SIGINT)
    err = child.finish()
    assert child.proc.returncode == cli.EXIT_INTERRUPTED
    assert MAIN_OUTPUT_OFF in err and "Traceback" not in err


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
        from otii_usb import UnsupportedDeviceError
        from otii_usb.interfaces.gateway import DeviceGateway
        sim = SimulatedOtii(SimulationConfig(device_type="Arc"))
        gateway = DeviceGateway(bound_factory(sim), SafeDefaults())
        with pytest.raises(UnsupportedDeviceError):
            gateway.ensure_safe()
        assert not any(r.command == Command.SET_PROPERTY for r in sim.requests)
