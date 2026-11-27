# errors.py: the exception hierarchy of the driver
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
# Device, transport and protocol errors derive from OtiiError, so callers can
# catch one type; invalid arguments raise ValueError.
#   - TransportError: the port is missing, busy or unplugged;
#   - ProtocolError: protocol violations and timeouts;
#   - CommandError: a non-zero answer from the device;
#   - UnsupportedDeviceError: a device that is not on the tested list
#     (REQ-SAFE-DEV-001);
#   - ReadOnlyPropertyError: a write to a property this driver does not write
#     (REQ-SAFE-DEV-003);
#   - OperationCancelledError: a blocking call aborted by the cancel event
#     (REQ-SAFE-OUT-004).

"""Exception hierarchy: device, transport and protocol errors derive from :class:`OtiiError`."""


class OtiiError(Exception):
    """Base class for all otii_usb errors."""


class TransportError(OtiiError):
    """The byte transport failed (I/O error, device unplugged, ...)."""


class DeviceNotFoundError(TransportError):
    """No matching Otii device is attached."""


class UnsupportedDeviceError(OtiiError):
    """The attached device is not on the list of tested devices; it is not operated."""


class ReadOnlyPropertyError(OtiiError, PermissionError):
    """A property that this driver does not write was asked to be set (REQ-SAFE-DEV-003)."""


class PortBusyError(TransportError):
    """The serial port is exclusively held by another process."""


class OperationCancelledError(OtiiError):
    """A blocking device operation was cancelled (e.g. because the program is shutting down)."""


class ProtocolError(OtiiError):
    """The device sent something that violates the protocol."""


class ResponseTimeoutError(ProtocolError):
    """The device did not answer a request in time."""


class CommandError(ProtocolError):
    """The device answered a request with a non-zero error code."""

    def __init__(self, command: int, code: int) -> None:
        self.command = command
        self.code = code
        name = getattr(command, "name", f"0x{command:02x}")
        super().__init__(f"command {name} failed with error code {code}")
