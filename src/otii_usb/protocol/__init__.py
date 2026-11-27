# protocol/__init__.py: the device's binary wire protocol
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
# constants.py names the commands, packet types, properties and channels;
# framing.py wraps payloads in frames; packets.py types the payloads;
# session.py runs requests and dispatches streamed data.

"""Wire protocol: constants, framing, packets and the request/response session."""
from .constants import CHANNEL_UNITS, ChannelId, Command, PacketType, PropertyAttr, PropertyId
from .framing import FrameDecoder, encode_frame
from .packets import (Alert, DataPacket, Packet, PropertyUpdate, Request, Response, SampleBlock,
                      UnknownPacket, parse_packet)
from .session import HelloInfo, ProgressCallback, Session

__all__ = ["CHANNEL_UNITS", "Alert", "ChannelId", "Command", "DataPacket", "FrameDecoder",
           "HelloInfo", "Packet", "PacketType", "PropertyAttr", "PropertyId", "PropertyUpdate",
           "ProgressCallback", "Request", "Response", "SampleBlock", "Session", "UnknownPacket", "encode_frame",
           "parse_packet"]
