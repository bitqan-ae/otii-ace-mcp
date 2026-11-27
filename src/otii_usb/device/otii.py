# otii.py: OtiiDevice, the facade for one connected Otii
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
# open() finds and opens the USB device, connect() takes any Transport;
# both run the handshake and refuse unsupported devices (REQ-SAFE-DEV-001).
#
# Control: set_output, set_voltage_setpoint and set_current_limit read every
# write back (REQ-SAFE-DEV-002) and check the limits (REQ-SAFE-DEV-004).
# apply_safe_defaults switches the main and expansion outputs off and sets
# the voltage and current limit (REQ-SAFE-OUT-001). shutdown switches the
# main output off first, then stops a recording left running
# (REQ-SAFE-DEV-005) and applies the defaults. power_cycle always ends with
# the output on (REQ-SAFE-OUT-008).
#
# Measurement: record() returns numpy arrays, measure() statistics,
# stream() feeds any SampleSink, capture() writes a CSV file and/or a PNG
# plot from one recording (REQ-MEAS-REC-003).

"""High-level device facade."""
import logging
import math
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import struct
import threading
from collections.abc import Sequence
from functools import cached_property
from types import TracebackType
from typing import Self

from ..errors import OtiiError, ProtocolError

from ..measurement.csv_log import DEFAULT_ROW_RATE, ColumnSink, CsvSink
from ..measurement.recorder import Recorder, StreamResult
from ..measurement.sinks import SampleSink, TeeSink
from ..report.plot import PlotInfo, PlotSink, render_png
from ..measurement.recording import Recording
from ..measurement.stats import MeasurementSummary
from ..protocol.constants import ChannelId, Command
from ..protocol.packets import Alert
from ..protocol.session import HelloInfo, ProgressCallback, Session
from ..transport.base import Transport
from ..transport.discovery import find_otii_port
from ..transport.serial import SerialTransport
from .channels import ChannelTable
from .models import (CaptureResult, DeviceInfo, OutputChange, PowerCycleResult, SafeDefaults,
                     SafeStateReport)
from .properties import Properties, PropertyClient, PropertySpec
from .support import SupportedDevice, identify

MAIN_CHANNELS: tuple[ChannelId, ...] = (ChannelId.MV, ChannelId.MC, ChannelId.MP)
log = logging.getLogger(__name__)


class OtiiDevice:
    """One connected Otii. Use :meth:`open` (USB) or :meth:`connect` (any transport)."""

    def __init__(self, session: Session, hello: HelloInfo, *, port: str) -> None:
        """Wrap an established session. Raises UnsupportedDeviceError for untested devices.

        Identification only reads; nothing is written to a device that is not supported.
        """
        self._session = session
        self._hello = hello
        self._port = port
        self._properties = PropertyClient(session)
        self._model: SupportedDevice = identify(hello.device_type, hello.protocol_version,
                                                self.get(Properties.FIRMWARE_VERSION))

    # ------------------------------------------------------------ construction

    @classmethod
    def connect(cls, transport: Transport, *, port: str | None = None,
                cancel: threading.Event | None = None) -> Self:
        session = Session(transport, cancel=cancel)
        try:
            return cls(session, session.handshake(), port=port or transport.name)
        except BaseException:
            session.close()
            raise

    @classmethod
    def open(cls, path: str | None = None, *, cancel: threading.Event | None = None) -> Self:
        """Open the Otii at ``path``, or the only attached one."""
        path = path or find_otii_port()
        return cls.connect(SerialTransport(path), port=path, cancel=cancel)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()

    # ------------------------------------------------------------ generic access

    @property
    def port(self) -> str:
        return self._port

    @property
    def hello(self) -> HelloInfo:
        return self._hello

    @property
    def alerts(self) -> list[Alert]:
        """Alert packets the device sent during this connection."""
        return list(self._session.alerts)

    def get[T](self, spec: PropertySpec[T]) -> T:
        return self._properties.get(spec)

    def set[T](self, spec: PropertySpec[T], value: T) -> None:
        self._properties.set(spec, value)

    @cached_property
    def channels(self) -> ChannelTable:
        return ChannelTable.parse(self.get(Properties.CHANNEL_INFO))

    def wait(self, duration: float, progress: ProgressCallback | None = None) -> None:
        """Idle for ``duration`` seconds while keeping the link alive."""
        self._session.pump(duration, progress)

    # ------------------------------------------------------------ identity / state

    @property
    def model(self) -> str:
        """Product name of the identified device, e.g. ``"Otii Ace Pro"``."""
        return self._model.model

    @property
    def name(self) -> str:
        return self.get(Properties.NAME)

    @property
    def firmware_version(self) -> str:
        return self.get(Properties.FIRMWARE_VERSION)

    @property
    def output_enabled(self) -> bool:
        return self.get(Properties.MAIN_OUTPUT)

    @property
    def voltage_setpoint(self) -> float:
        return self.get(Properties.MAIN_VOLTAGE)

    @property
    def current_limit(self) -> float:
        return self.get(Properties.CURRENT_LIMIT)

    def info(self) -> DeviceInfo:
        return DeviceInfo(model=self.model, name=self.name, device_type=self._hello.device_type,
                          serial_number=self.get(Properties.SERIAL_NUMBER),
                          board_serial=self.get(Properties.BOARD_SERIAL),
                          hardware_revision=self.get(Properties.HARDWARE_VERSION),
                          firmware=self.firmware_version,
                          bootloader=self.get(Properties.BOOT_VERSION), port=self.port,
                          protocol_version=self._hello.protocol_version,
                          max_payload=self._hello.max_payload,
                          output_enabled=self.output_enabled,
                          voltage_setpoint=self.voltage_setpoint,
                          current_limit=self.current_limit)

    # ------------------------------------------------------------ control

    def set_output(self, enabled: bool) -> OutputChange:
        """Switch the main output and confirm the new state by reading it back."""
        before = self.output_enabled
        self.set(Properties.MAIN_OUTPUT, enabled)
        after = self.output_enabled
        if after != enabled:
            raise ProtocolError(f"main output did not switch {'on' if enabled else 'off'}")
        return OutputChange(before, after)

    def set_voltage_setpoint(self, volts: float) -> float:
        maximum = self.get(Properties.MAIN_VOLTAGE_MAX)
        if not 0 < volts <= maximum:
            raise ValueError(f"voltage {volts} V outside 0..{maximum} V")
        self.set(Properties.MAIN_VOLTAGE, volts)
        return self._verify(Properties.MAIN_VOLTAGE, volts, tolerance=1e-4)

    def set_current_limit(self, amps: float) -> float:
        if not amps > 0:
            raise ValueError("current limit must be positive")
        self.set(Properties.CURRENT_LIMIT, amps)
        return self._verify(Properties.CURRENT_LIMIT, amps, tolerance=1e-4)

    def _verify(self, spec: PropertySpec[float], expected: float, *, tolerance: float) -> float:
        actual = self.get(spec)
        if not math.isclose(actual, expected, abs_tol=tolerance):
            raise ProtocolError(f"{spec.name}: wrote {expected}, read back {actual}")
        return actual

    def apply_safe_defaults(self, defaults: SafeDefaults = SafeDefaults()) -> SafeStateReport:
        """Turn the outputs off, then set the voltage setpoint and current limit."""
        self.set_output(False)
        if defaults.expansion_output_off:
            self.set(Properties.EXPANSION_OUTPUT_VOLTAGE, 0.0)
            expansion = self._verify(Properties.EXPANSION_OUTPUT_VOLTAGE, 0.0, tolerance=1e-3)
        else:
            expansion = self.get(Properties.EXPANSION_OUTPUT_VOLTAGE)
        voltage = self.set_voltage_setpoint(defaults.voltage)
        limit = self.set_current_limit(defaults.current_limit)
        return SafeStateReport(main_output=self.output_enabled, expansion_output_voltage=expansion,
                               voltage_setpoint=voltage, current_limit=limit)

    def stop_recording(self) -> None:
        """Best effort: stop a recording left running (e.g. by an interrupted program).

        Never raises a device error: failures are logged, so the safe state can still follow.
        """
        try:
            self._session.request(Command.RECORD_STOP)
            for channel, info in self.channels.items():
                if info.is_analog:
                    self._session.request(Command.SET_CHANNEL, struct.pack("<II", channel, 0))
        except OtiiError as e:
            log.warning("could not stop a recording: %s", e)

    def shutdown(self, defaults: SafeDefaults = SafeDefaults()) -> SafeStateReport:
        """Bring the device into the safe state for leaving it unattended.

        The main output goes off first, before anything that could fail; then a leftover
        recording is stopped and the safe defaults are applied and verified.
        """
        try:
            self.set(Properties.MAIN_OUTPUT, False)
        except OtiiError as e:
            log.warning("switching the main output off failed, retrying: %s", e)
        self.stop_recording()
        return self.apply_safe_defaults(defaults)

    def power_cycle(self, off_time: float = 1.0,
                    progress: ProgressCallback | None = None) -> PowerCycleResult:
        """Main output off, wait ``off_time`` seconds, on. Always ends with the output on."""
        if off_time < 0:
            raise ValueError("off_time must not be negative")
        was_enabled = self.set_output(False).before
        self.wait(off_time, progress)
        return PowerCycleResult(was_enabled, off_time, self.set_output(True).after)

    # ------------------------------------------------------------ measurement

    def record(self, duration: float, channels: Sequence[int] = MAIN_CHANNELS,
               progress: ProgressCallback | None = None) -> Recording:
        """Record into memory: numpy arrays per channel."""
        if duration <= 0:
            raise ValueError("duration must be positive")
        return Recorder(self._session, self.channels).record(channels, duration, progress)

    def stream(self, sink: SampleSink, duration: float, channels: Sequence[int] = MAIN_CHANNELS,
               progress: ProgressCallback | None = None) -> StreamResult:
        """Feed ``duration`` seconds of samples (numpy arrays per packet) into ``sink``."""
        if duration <= 0:
            raise ValueError("duration must be positive")
        return Recorder(self._session, self.channels).stream(channels, duration, sink, progress)

    def measure(self, duration: float = 0.5, channels: Sequence[int] = MAIN_CHANNELS,
                progress: ProgressCallback | None = None) -> MeasurementSummary:
        return self.record(duration, channels, progress).summarize()

    def capture(self, duration: float, *, csv: str | Path | None = None,
                png: str | Path | None = None, row_rate: float | None = DEFAULT_ROW_RATE,
                progress: ProgressCallback | None = None) -> CaptureResult:
        """Record voltage, current and power to a CSV file and/or a PNG plot.

        Both outputs are fed from the same recording. ``row_rate`` CSV rows per second
        (averaged) or every sample with ``row_rate=None``. On interruption the CSV file is
        still complete and closed; the plot is only rendered after a full recording.
        """
        if duration <= 0:
            raise ValueError("duration must be positive")
        if csv is None and png is None:
            raise ValueError("select at least one output (csv and/or png)")
        plot_info = PlotInfo(model=self.model, serial_number=self.get(Properties.SERIAL_NUMBER),
                             firmware=self.firmware_version, started=datetime.now().astimezone(),
                             duration=duration, sample_rate=0, bin_rate=0.0,
                             voltage_setpoint=self.voltage_setpoint)
        sinks: list[ColumnSink] = []
        with ExitStack() as files:
            csv_sink = None
            if csv is not None:
                stream = files.enter_context(Path(csv).open("w", newline=""))
                csv_sink = CsvSink(stream, row_rate=row_rate)
                sinks.append(csv_sink)
            plot_sink = PlotSink(duration) if png is not None else None
            if plot_sink is not None:
                sinks.append(plot_sink)
            result = Recorder(self._session, self.channels).stream(
                MAIN_CHANNELS, duration, TeeSink(*sinks), progress)
        summary = sinks[0].summary(duration, result.missing_packets)
        png_path = None
        if plot_sink is not None and png is not None:
            png_path = render_png(png, plot_sink.bins(), replace(
                plot_info, sample_rate=plot_sink.sample_rate, bin_rate=plot_sink.bin_rate), summary)
        return CaptureResult(started=plot_info.started, duration=duration,
                             sample_rate=sinks[0].sample_rate, summary=summary,
                             csv_path=Path(csv).resolve() if csv is not None else None,
                             csv_rows=csv_sink.rows if csv_sink else 0,
                             csv_row_rate=csv_sink.row_rate if csv_sink else 0.0,
                             png_path=png_path)



__all__ = ["MAIN_CHANNELS", "OtiiDevice"]
