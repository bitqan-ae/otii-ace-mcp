# common.py: helpers shared by the command line, shell and MCP server
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
# device_factory returns a callable that opens a fresh connection per call,
# to the USB device or to one shared simulator, optionally bound to a
# cancel event. format_si prints values with engineering prefixes.

"""Helpers shared by the CLI and the MCP server."""
import threading
from collections.abc import Callable

from ..device.otii import OtiiDevice
from ..simulator import SimulatedOtii

type DeviceFactory = Callable[[], OtiiDevice]


def device_factory(port: str | None = None, *, simulate: bool = False,
                   cancel: threading.Event | None = None) -> DeviceFactory:
    """Return a callable that opens a fresh connection on every call.

    In simulation mode one emulated device is shared by all connections, so its state
    (output, setpoint, ...) persists like on real hardware. Connections made while
    ``cancel`` is set abort their blocking operations.
    """
    if simulate:
        simulator = SimulatedOtii()
        return lambda: OtiiDevice.connect(simulator, cancel=cancel)
    return lambda: OtiiDevice.open(port, cancel=cancel)


def format_si(value: float, unit: str) -> str:
    for factor, prefix in ((1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")):
        if abs(value) >= factor:
            return f"{value / factor:.4f} {prefix}{unit}"
    return f"{value / 1e-9:.4f} n{unit}"
