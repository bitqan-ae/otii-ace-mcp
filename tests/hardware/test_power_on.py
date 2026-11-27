# test_power_on.py: power-on recording on an attached Otii Ace Pro
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
# Run with `python -m pytest --hardware --switch-output tests/hardware`.
# This test POWERS THE DEVICE UNDER TEST for half a second at 5 V with a
# 1 A current limit: it applies the safe state with these values, makes a power-on
# recording and checks that the property write during the recording keeps
# the data stream intact, that the output was off before the marked command
# and on afterwards, and that it is off at the end (REQ-MEAS-REC-004). Nothing device-specific is
# written to the repository (DC-TOOL-DOC-004).

import numpy as np
import pytest

from otii_usb import OtiiDevice
from otii_usb.device.models import SafeDefaults

pytestmark = pytest.mark.switches_output
LIMITS = SafeDefaults(voltage=5.0, current_limit=1.0)


def test_power_on_recording(tmp_path):
    with OtiiDevice.open() as otii:
        try:
            report = otii.apply_safe_defaults(LIMITS)
            assert report.voltage_setpoint == pytest.approx(5.0, abs=1e-3)
            assert report.current_limit == pytest.approx(1.0, abs=1e-3)
            result = otii.capture(0.5, csv=tmp_path / "on.csv", png=tmp_path / "on.png",
                                  row_rate=None, power_on=True, pre_trigger=0.05)
            assert otii.output_enabled is False
        finally:
            otii.shutdown()
    sent = result.power_on.command_sent
    assert result.summary.missing_packets == 0
    assert 0.05 <= sent < 0.5 and result.power_on.on_at_end
    rows = np.loadtxt(result.csv_path, delimiter=",", skiprows=1)
    assert rows.shape[1] == 5
    before, after = rows[rows[:, 4] == 0], rows[rows[:, 4] == 1]
    assert before[-1, 0] < sent <= after[0, 0] + 1 / 50_000
    assert np.all(before[:, 1] < 0.05)          # output off up to the command
    assert after[-1000:, 1].mean() > 4.5        # and on (5 V) by the end
