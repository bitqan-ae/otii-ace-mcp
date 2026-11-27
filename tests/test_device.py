# test_device.py: property codecs, the channel table and OtiiDevice
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
# Codecs and the write whitelist (SAF-006), channel records and numpy
# sample decoding (SW-006, SW-007), identity, measurements with the output
# on and off, read-back verified control (SAF-005), the power-cycle
# sequence (SAF-010), the safe defaults (SAF-001) and limits (SAF-007).

import numpy as np
import pytest

from otii_usb import ChannelId, OtiiDevice, SafeDefaults
from otii_usb.device import (ChannelInfo, ChannelKind, ChannelTable, Properties, PropertyClient,
                             ScaledIntCodec)
from otii_usb.protocol import Command, Session
from otii_usb.simulator import SimulatedOtii, SimulationConfig


class TestCodecs:
    def test_scaled_int_roundtrip(self):
        codec = ScaledIntCodec(1e-6)
        assert codec.encode(3.0) == (3_000_000).to_bytes(4, "little")
        assert codec.decode(codec.encode(4.2)) == pytest.approx(4.2)

    def test_read_only_property_is_refused(self, simulator):
        session = Session(simulator)
        session.handshake()
        with pytest.raises(PermissionError):
            PropertyClient(session).set(Properties.NAME, "x")


class TestChannels:
    def test_record_roundtrip(self):
        info = ChannelInfo(1, ChannelKind.ANALOG, True, False, 0.0, 50_000, 50)
        assert ChannelTable.parse(info.to_record())[1] == info

    def test_scaled_int_samples(self):
        info = ChannelInfo(1, ChannelKind.ANALOG, False, True, 1e-6, 1000)
        raw = np.array([3_000_000, -1], dtype="<i4").tobytes()
        np.testing.assert_allclose(info.decode_samples(raw, 2), [3.0, -1e-6])

    def test_encode_decode_inverse(self):
        info = ChannelInfo(0, ChannelKind.ANALOG, True, False, 0.0, 1000)
        values = np.array([0.5, -0.25, 1e-3])
        np.testing.assert_allclose(info.decode_samples(info.encode_samples(values), 3), values,
                                   rtol=1e-6)

    def test_non_analog_rejected(self):
        with pytest.raises(ValueError):
            ChannelInfo(0x16, ChannelKind.DIGITAL, False, False, 0.0, 0).decode_samples(bytes(4), 1)

    def test_bad_table_length(self):
        with pytest.raises(Exception):
            ChannelTable.parse(bytes(15))


class TestDevice:
    def test_info(self, device):
        info = device.info()
        assert (info.name, info.firmware, info.output_enabled) == ("Ace", "3.2.5", False)
        assert info.voltage_setpoint == pytest.approx(3.0)
        assert info.current_limit == pytest.approx(1.2)

    def test_channel_table(self, device):
        assert {ChannelId.MC, ChannelId.MV, ChannelId.MP} <= set(device.channels)

    def test_measure_output_off(self, device):
        summary = device.measure(0.05)
        assert summary.voltage.mean == pytest.approx(0.0045)
        assert summary.current.mean == 0.0
        assert summary.voltage.count >= 0.05 * 5_000 * 0.8

    def test_measure_output_on(self, device):
        device.set_output(True)
        summary = device.measure(0.05)
        assert summary.voltage.mean == pytest.approx(3.0)
        assert summary.current.mean == pytest.approx(3.0 / 1000)
        assert summary.power.mean == pytest.approx(3.0 * 3.0 / 1000)

    def test_record_returns_numpy(self, device):
        rec = device.record(0.02)
        assert isinstance(rec[ChannelId.MV], np.ndarray)
        assert rec[ChannelId.MV].dtype == np.float64
        assert rec.timestamps(ChannelId.MV)[1] == pytest.approx(1 / 5_000)

    def test_recording_cleans_up(self, device, simulator):
        device.measure(0.02)
        assert not simulator.state.recording
        assert not simulator.state.enabled_channels

    def test_set_output(self, device):
        change = device.set_output(True)
        assert (change.before, change.after) == (False, True)

    def test_power_cycle_sequence(self, device, simulator):
        device.set_output(True)
        simulator.requests.clear()
        result = device.power_cycle(0.05)
        assert (result.was_enabled, result.is_enabled) == (True, True)
        sets = [r.args for r in simulator.requests if r.command == Command.SET_PROPERTY]
        assert sets == [Properties.MAIN_OUTPUT.wire_id.to_bytes(4, "little") + v
                        for v in (b"\0\0\0\0", b"\1\0\0\0")]

    def test_power_cycle_from_off_ends_on(self, device):
        assert device.power_cycle(0.01).is_enabled

    def test_apply_safe_defaults(self, simulator):
        simulator.state.output_enabled = True
        simulator.state.voltage_setpoint = 12.0
        simulator.state.expansion_output_voltage = 3.3
        with OtiiDevice.connect(simulator) as dev:
            report = dev.apply_safe_defaults()
        assert simulator.state.output_enabled is False
        assert simulator.state.expansion_output_voltage == 0.0
        assert simulator.state.voltage_setpoint == pytest.approx(5.0)
        assert simulator.state.current_limit == pytest.approx(0.5)
        assert report.to_dict()["main_output"] == "off"

    def test_safe_defaults_can_keep_expansion_output(self, simulator):
        simulator.state.expansion_output_voltage = 3.3
        with OtiiDevice.connect(simulator) as dev:
            dev.apply_safe_defaults(SafeDefaults(expansion_output_off=False))
        assert simulator.state.expansion_output_voltage == pytest.approx(3.3)

    def test_voltage_above_device_maximum_rejected(self, device):
        with pytest.raises(ValueError):
            device.set_voltage_setpoint(30.0)

    def test_current_limit_clamps_simulated_current(self):
        sim = SimulatedOtii(SimulationConfig(sample_rate=5_000, load_resistance=1.0))
        with OtiiDevice.connect(sim) as dev:
            dev.apply_safe_defaults()
            dev.set_output(True)
            assert dev.measure(0.02).current.mean == pytest.approx(0.5)

    def test_state_survives_reconnect(self, simulator):
        with OtiiDevice.connect(simulator) as dev:
            dev.set_output(True)
        with OtiiDevice.connect(simulator) as dev:
            assert dev.output_enabled
