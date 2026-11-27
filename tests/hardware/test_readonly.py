# test_readonly.py: read-only checks against an attached Otii Ace Pro
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
# Run with `python -m pytest --hardware tests/hardware`. These tests never
# switch the output or change a setting (they read properties and make short
# recordings), and they check that the output state is the same afterwards.
# They cover identification (REQ-SAFE-DEV-001, REQ-UI-CLI-004), the protocol
# on real traffic (REQ-PROT-LINK-001, -002) and recording to CSV and PNG
# (REQ-MEAS-REC-001 to -003). Nothing device-specific is written to the
# repository (DC-TOOL-DOC-004).

import numpy as np
import pytest

from otii_usb import ChannelId, OtiiDevice
from otii_usb.device import SUPPORTED_DEVICES


@pytest.fixture(scope="module")
def otii():
    with OtiiDevice.open() as device:
        before = device.output_enabled
        yield device
        assert device.output_enabled == before, "a read-only test changed the output"


def test_identified_as_tested_device(otii):
    info = otii.info()
    assert otii.model in {d.model for d in SUPPORTED_DEVICES}
    assert info.protocol_version == 3 and info.firmware == "3.2.5"
    assert info.serial_number and info.board_serial


def test_channel_table(otii):
    for channel in (ChannelId.MC, ChannelId.MV, ChannelId.MP):
        info = otii.channels[channel]
        assert info.is_analog and info.sample_rate == 50_000


def test_measurement_streams_without_gaps(otii):
    recording = otii.record(0.2)
    assert recording.missing_packets == 0
    assert recording[ChannelId.MV].size >= 0.2 * 50_000 * 0.9
    assert np.all(np.isfinite(recording[ChannelId.MC]))


def test_capture_csv_and_png(otii, tmp_path):
    result = otii.capture(0.2, csv=tmp_path / "hw.csv", png=tmp_path / "hw.png")
    assert result.csv_rows >= 190 and result.png_path.stat().st_size > 10_000
