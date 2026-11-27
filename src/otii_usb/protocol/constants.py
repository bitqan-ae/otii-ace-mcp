# constants.py: command ids, packet types, property ids and channel ids
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
# Only the commands listed here are ever sent to the device
# (REQ-SAFE-DEV-007); the full reference with encodings is
# docs/contributor/protocol.md. Property ids
# combine a base id with an attribute selector (min, max, enum, float32,
# step) in bits 10..13.

"""Wire-level constants of the Otii firmware 3.x binary protocol."""
from enum import IntEnum

FRAME_MAGIC = 0x7FB52CA3
TEXT_INIT = b"\rinit\r\n"
"""Text-mode wake-up line; sent if the device does not answer the binary hello."""
KEEPALIVE_ARGS = bytes(8)


class Command(IntEnum):
    PING = 0x0A
    HELLO = 0x14
    GET_PROPERTY = 0x64
    SET_PROPERTY = 0x66
    SET_CHANNEL = 0x78
    RECORD_START = 0x7C
    RECORD_STOP = 0x7E


class PacketType(IntEnum):
    RESPONSE = 0xFF99030E
    PROPERTY_UPDATE = 0xFF2FF2CE
    ALERT = 0xFFC593CA
    DATA = 0xFF2A8369


class PropertyId(IntEnum):
    NAME = 0x01
    MAIN_OUTPUT = 0x09
    MAIN_VOLTAGE = 0x0B
    MAX_CURRENT = 0x0C
    EXPANSION_VOLTAGE = 0x33
    EXPANSION_OUTPUT_VOLTAGE = 0x34
    HARDWARE_VERSION = 0x80
    FIRMWARE_VERSION = 0x81
    UID = 0x82
    BOARD_SERIAL = 0x84
    BOOT_VERSION = 0x87
    CHANNEL_INFO = 0x8D


class PropertyAttr(IntEnum):
    """Selector OR-ed onto a :class:`PropertyId` to address one aspect of a property."""
    VALUE = 0x0000
    MIN = 0x0400
    MAX = 0x0800
    ENUM = 0x0C00
    FLOAT = 0x1000
    STEP = 0x1400


class ChannelId(IntEnum):
    """Recording channels, named by their Otii short names."""
    MC = 0x00      # main current [A]
    MV = 0x01      # main voltage [V]
    AC = 0x02
    AV = 0x03
    SN = 0x04
    SP = 0x05
    MP = 0x06      # main power [W]
    AP = 0x07
    MC1 = 0x0C
    MC2 = 0x0D
    MC3 = 0x0E
    VB = 0x10
    VJ = 0x11
    VPOE = 0x12
    VPWR = 0x13
    TP = 0x14
    TM = 0x15
    GPI = 0x16

    @property
    def short_name(self) -> str:
        return self.name.lower()


CHANNEL_UNITS: dict[ChannelId, str] = {ChannelId.MC: "A", ChannelId.MV: "V", ChannelId.MP: "W"}


def channel_name(channel: int) -> str:
    try:
        return ChannelId(channel).short_name
    except ValueError:
        return f"ch{channel:#04x}"
