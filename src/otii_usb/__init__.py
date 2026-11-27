# otii_usb/__init__.py: package entry point - the Otii Ace Pro driver's
# public API in one import
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
# Re-exports what most users need: OtiiDevice (open the device, measure,
# record, switch outputs), SafeDefaults, the measurement result types,
# ChannelId and the exception hierarchy. Everything else lives in the
# domain packages: transport, protocol, device, measurement, report,
# simulator and interfaces.

"""Standalone USB driver for Qoitech Otii Ace / Arc power analyzers.

>>> from otii_usb import OtiiDevice
>>> with OtiiDevice.open() as otii:
...     print(otii.measure(0.5).to_dict())
"""
from .device import MAIN_CHANNELS, DeviceInfo, OtiiDevice, SafeDefaults
from .errors import (CommandError, DeviceNotFoundError, OperationCancelledError, OtiiError,
                     PortBusyError, ProtocolError, ResponseTimeoutError, TransportError,
                     UnsupportedDeviceError)
from .measurement import MeasurementSummary, Recording, Stats
from .protocol import ChannelId

__version__ = "0.1.0"

__all__ = ["MAIN_CHANNELS", "ChannelId", "CommandError", "DeviceInfo", "DeviceNotFoundError",
           "MeasurementSummary", "OtiiDevice", "OtiiError", "PortBusyError", "ProtocolError",
           "Recording", "ResponseTimeoutError", "SafeDefaults", "Stats", "TransportError",
           "OperationCancelledError", "UnsupportedDeviceError",
           "__version__"]
