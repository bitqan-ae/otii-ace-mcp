# device/__init__.py: the device model
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
# OtiiDevice and what it is built from: typed properties, the channel
# table, the list of tested devices and the result value objects.

"""Device model: properties, channels and the :class:`OtiiDevice` facade."""
from .channels import ChannelInfo, ChannelKind, ChannelTable
from .models import (DeviceInfo, OutputChange, PowerCycleResult, SafeDefaults, SafeStateReport)
from .otii import MAIN_CHANNELS, OtiiDevice
from .support import SUPPORTED_DEVICES, SupportedDevice, identify
from .properties import (BoolCodec, BytesCodec, Codec, Float32Codec, Int32Codec, Properties,
                         PropertyClient, PropertySpec, ScaledIntCodec, StringCodec)

__all__ = ["SUPPORTED_DEVICES", "SupportedDevice", "identify", "MAIN_CHANNELS", "BoolCodec", "BytesCodec", "ChannelInfo", "ChannelKind",
           "ChannelTable", "Codec", "DeviceInfo", "Float32Codec", "Int32Codec", "OtiiDevice",
           "OutputChange", "PowerCycleResult", "Properties", "PropertyClient", "PropertySpec",
           "SafeDefaults", "SafeStateReport", "ScaledIntCodec", "StringCodec"]
