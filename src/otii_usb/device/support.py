# support.py: which devices the driver operates
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
# A device is identified by the type in the hello reply ("Ace"), the
# protocol version and the firmware version, all read-only. Only entries of
# SUPPORTED_DEVICES are operated; the only tested device today is the Otii
# Ace Pro with firmware 3.2.x. Anything else raises UnsupportedDeviceError
# before a single write (SAF-008).

"""Which devices this driver operates."""
from dataclasses import dataclass

from ..errors import UnsupportedDeviceError


@dataclass(frozen=True, slots=True)
class SupportedDevice:
    model: str
    device_type: str
    protocol_versions: frozenset[int]
    firmware_prefixes: tuple[str, ...]

    def matches(self, device_type: str, protocol_version: int, firmware: str) -> bool:
        return (device_type == self.device_type and protocol_version in self.protocol_versions
                and firmware.startswith(self.firmware_prefixes))


SUPPORTED_DEVICES: tuple[SupportedDevice, ...] = (
    SupportedDevice(model="Otii Ace Pro", device_type="Ace", protocol_versions=frozenset({3}),
                    firmware_prefixes=("3.2.",)),
)


def identify(device_type: str, protocol_version: int, firmware: str) -> SupportedDevice:
    """Return the matching entry of :data:`SUPPORTED_DEVICES`, or raise."""
    for entry in SUPPORTED_DEVICES:
        if entry.matches(device_type, protocol_version, firmware):
            return entry
    tested = ", ".join(f"{d.model} (type {d.device_type!r}, firmware {'/'.join(d.firmware_prefixes)}x)"
                       for d in SUPPORTED_DEVICES)
    raise UnsupportedDeviceError(
        f"unsupported device: type {device_type!r}, protocol {protocol_version}, firmware "
        f"{firmware!r}; tested devices: {tested}")
