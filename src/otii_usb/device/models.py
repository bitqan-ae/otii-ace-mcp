# models.py: value objects returned by OtiiDevice
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
# DeviceInfo (identity and state), OutputChange, PowerCycleResult,
# SafeDefaults (the safe state: outputs off, 5 V, 500 mA), SafeStateReport
# and CaptureResult (files written by a recording). All are frozen
# dataclasses with to_dict() for JSON and MCP output.

"""Value objects returned by :class:`~otii_usb.device.otii.OtiiDevice`."""
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..measurement.stats import MeasurementSummary


def on_off(value: bool) -> str:
    return "on" if value else "off"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    model: str
    name: str
    device_type: str
    serial_number: str
    board_serial: str
    hardware_revision: str
    firmware: str
    bootloader: str
    port: str
    protocol_version: int
    max_payload: int
    output_enabled: bool
    voltage_setpoint: float
    current_limit: float

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def identity(self) -> str:
        """One line naming the device: model, serial numbers, firmware, protocol, port."""
        return (f"{self.model}  S/N {self.serial_number} · board {self.board_serial} · "
                f"fw {self.firmware} · protocol {self.protocol_version} · {self.port}")


@dataclass(frozen=True, slots=True)
class OutputChange:
    before: bool
    after: bool

    def to_dict(self) -> dict:
        return {"before": on_off(self.before), "after": on_off(self.after)}


@dataclass(frozen=True, slots=True)
class PowerCycleResult:
    was_enabled: bool
    off_time: float
    is_enabled: bool

    def to_dict(self) -> dict:
        return {"before": on_off(self.was_enabled), "off_s": self.off_time,
                "after": on_off(self.is_enabled)}


@dataclass(frozen=True, slots=True)
class SafeDefaults:
    """State the device is forced into before use.

    The main output is always switched off. The expansion-port power output is switched off
    (0 V) unless ``expansion_output_off`` is False. Then the main voltage setpoint and the
    current limit are set.
    """
    voltage: float = 5.0
    current_limit: float = 0.5
    expansion_output_off: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.voltage:
            raise ValueError("voltage must be positive")
        if not 0 < self.current_limit:
            raise ValueError("current limit must be positive")


@dataclass(frozen=True, slots=True)
class SafeStateReport:
    main_output: bool
    expansion_output_voltage: float
    voltage_setpoint: float
    current_limit: float

    def to_dict(self) -> dict:
        return {"main_output": on_off(self.main_output),
                "expansion_output_voltage_v": self.expansion_output_voltage,
                "voltage_setpoint_v": self.voltage_setpoint,
                "current_limit_a": self.current_limit}


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Files written by :meth:`~otii_usb.device.otii.OtiiDevice.capture`."""
    started: datetime
    duration: float
    sample_rate: int
    summary: MeasurementSummary
    csv_path: Path | None = None
    csv_rows: int = 0
    csv_row_rate: float = 0.0
    png_path: Path | None = None

    def to_dict(self) -> dict:
        result: dict = {"started": self.started.isoformat(timespec="seconds"),
                        "duration_s": self.duration, "sample_rate_hz": self.sample_rate}
        if self.csv_path is not None:
            result["csv"] = {"path": str(self.csv_path), "rows": self.csv_rows,
                             "row_rate_hz": self.csv_row_rate}
        if self.png_path is not None:
            result["png"] = {"path": str(self.png_path)}
        return result | self.summary.to_dict()
