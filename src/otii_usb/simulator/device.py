# device.py: SimulatedOtii, an Otii Ace Pro emulated behind the
# Transport interface
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
# Decodes the host's frames with the real FrameDecoder, answers hello, ping,
# property get/set, channel and recording commands with the real packet
# encoders, and emits one data packet per elapsed millisecond while
# recording. Voltage is the setpoint while the output is on, current is
# voltage over a configurable load resistance, limited by the current
# limit, plus optional noise. Device state survives close(), like real
# hardware, so it can be reconnected. Used by the whole test suite and by
# --simulate (REQ-TEST-SIM-001).

"""In-memory Otii emulator implementing the :class:`~otii_usb.transport.Transport` interface."""
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..device.channels import ChannelInfo, ChannelKind, ChannelTable
from ..device.properties import Properties, PropertySpec
from ..errors import TransportError
from ..protocol.constants import ChannelId, Command, PropertyAttr
from ..protocol.framing import FrameDecoder, encode_frame
from ..protocol.packets import (DataPacket, Request, Response, SampleBlock, encode_data_packet,
                                encode_response, parse_request)
from ..transport.base import Transport

ERR_UNKNOWN_COMMAND = 1
ERR_UNKNOWN_PROPERTY = 0xFFFFFFFD
ERR_READ_ONLY = 2

_ANALOG_CHANNELS = (ChannelId.MC, ChannelId.MV, ChannelId.MP)


@dataclass
class SimulationConfig:
    name: str = "Ace"
    device_type: str = "Ace"
    feature_bits: int = 0x3F
    firmware: str = "3.2.5"
    bootloader: str = "3.2.2"
    hardware_revision: str = "rev 2"
    serial_number: str = "000000000000"
    board_serial: str = "00000000-00000000"
    protocol_version: int = 3
    max_payload: int = 32800
    sample_rate: int = 50_000
    output_enabled: bool = False
    voltage_setpoint: float = 3.0
    voltage_max: float = 25.0
    current_limit: float = 1.2
    expansion_output_voltage: float = 0.0
    load_resistance: float = 1000.0
    off_voltage: float = 0.0045
    noise: float = 0.0
    seed: int = 0
    trip_at_limit: bool = False
    """Emulate a protection that switches the output off when the load would exceed the
    current limit (otherwise the current is clamped to the limit)."""


@dataclass
class _State:
    output_enabled: bool
    voltage_setpoint: float
    current_limit: float
    expansion_output_voltage: float
    enabled_channels: set[int] = field(default_factory=set)
    recording: bool = False
    sequence: int = 0
    next_packet_at: float = 0.0


class SimulatedOtii(Transport):
    """Emulates an Otii Ace on the far end of the wire.

    The device state survives :meth:`close` (like real hardware keeping its output state), so
    the same instance can be reconnected.
    """

    def __init__(self, config: SimulationConfig | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config or SimulationConfig()
        self._clock = clock
        self._rng = np.random.default_rng(self.config.seed)
        self._decoder = FrameDecoder()
        self._outbox = bytearray()
        self.state = _State(self.config.output_enabled, self.config.voltage_setpoint,
                            self.config.current_limit, self.config.expansion_output_voltage)
        self.requests: list[Request] = []
        self.channels = ChannelTable({
            ch: ChannelInfo(channel=ch, kind=ChannelKind.ANALOG, float_samples=True, scaled=False,
                            scale=0.0, sample_rate=self.config.sample_rate, aux=50)
            for ch in _ANALOG_CHANNELS})
        self._handlers: dict[int, Callable[[bytes], bytes]] = {
            Command.HELLO: self._on_hello,
            Command.PING: lambda args: b"",
            Command.GET_PROPERTY: self._on_get,
            Command.SET_PROPERTY: self._on_set,
            Command.SET_CHANNEL: self._on_set_channel,
            Command.RECORD_START: self._on_record_start,
            Command.RECORD_STOP: self._on_record_stop,
        }

    @property
    def name(self) -> str:
        return "simulator"

    # ------------------------------------------------------------ Transport

    def write(self, data: bytes) -> None:
        for payload in self._decoder.feed(data):
            self._handle(parse_request(payload))

    def read(self, timeout: float) -> bytes:
        self._emit_samples()
        if not self._outbox and timeout > 0:
            time.sleep(min(timeout, 0.001))
            self._emit_samples()
        data = bytes(self._outbox)
        self._outbox.clear()
        return data

    def close(self) -> None:
        """Disconnect the virtual cable: stop streaming, drop buffered bytes."""
        self.state.recording = False
        self.state.enabled_channels.clear()
        self._outbox.clear()
        self._decoder = FrameDecoder()

    # ------------------------------------------------------------ request handling

    class _Fail(Exception):
        def __init__(self, code: int) -> None:
            self.code = code

    def _apply_protection(self) -> None:
        s, c = self.state, self.config
        if c.trip_at_limit and s.output_enabled and \
                s.voltage_setpoint / c.load_resistance >= s.current_limit:
            s.output_enabled = False

    def _handle(self, request: Request) -> None:
        self._apply_protection()
        self.requests.append(request)
        handler = self._handlers.get(request.command)
        try:
            if handler is None:
                raise self._Fail(ERR_UNKNOWN_COMMAND)
            response = Response(request.txn, 0, handler(request.args))
        except self._Fail as e:
            response = Response(request.txn, e.code, b"")
        self._send(encode_response(response))

    def _send(self, payload: bytes) -> None:
        self._outbox += encode_frame(payload)

    def _on_hello(self, args: bytes) -> bytes:
        c = self.config
        fourcc = c.device_type.ljust(4).encode("ascii")[:4][::-1]
        return struct.pack("<II", c.protocol_version, c.max_payload) + fourcc + \
            struct.pack("<I", c.feature_bits)

    def _property_table(self) -> dict[int, tuple[PropertySpec, Callable[[], object]]]:
        s, c = self.state, self.config
        specs: list[tuple[PropertySpec, Callable[[], object]]] = [
            (Properties.NAME, lambda: c.name),
            (Properties.FIRMWARE_VERSION, lambda: c.firmware),
            (Properties.BOOT_VERSION, lambda: c.bootloader),
            (Properties.HARDWARE_VERSION, lambda: c.hardware_revision),
            (Properties.SERIAL_NUMBER, lambda: c.serial_number),
            (Properties.BOARD_SERIAL, lambda: c.board_serial),
            (Properties.MAIN_OUTPUT, lambda: s.output_enabled),
            (Properties.MAIN_VOLTAGE, lambda: s.voltage_setpoint),
            (Properties.MAIN_VOLTAGE_MAX, lambda: c.voltage_max),
            (Properties.CURRENT_LIMIT, lambda: s.current_limit),
            (Properties.EXPANSION_OUTPUT_VOLTAGE, lambda: s.expansion_output_voltage),
            (Properties.CHANNEL_INFO, lambda: self.channels.encode()),
        ]
        return {spec.wire_id: (spec, getter) for spec, getter in specs}

    def _on_get(self, args: bytes) -> bytes:
        wire_id, _ = struct.unpack_from("<II", args)
        entry = self._property_table().get(wire_id)
        if entry is None:
            raise self._Fail(ERR_UNKNOWN_PROPERTY)
        spec, getter = entry
        return spec.codec.encode(getter())

    def _on_set(self, args: bytes) -> bytes:
        (wire_id,) = struct.unpack_from("<I", args)
        value = args[4:]
        entry = self._property_table().get(wire_id)
        if entry is None:
            raise self._Fail(ERR_UNKNOWN_PROPERTY)
        spec = entry[0]
        if spec.attr not in (PropertyAttr.VALUE, PropertyAttr.FLOAT) or not spec.writable:
            raise self._Fail(ERR_READ_ONLY)
        decoded = spec.codec.decode(value)
        match spec:
            case Properties.MAIN_OUTPUT:
                self.state.output_enabled = bool(decoded)
            case Properties.MAIN_VOLTAGE:
                self.state.voltage_setpoint = float(decoded)
            case Properties.CURRENT_LIMIT:
                self.state.current_limit = float(decoded)
            case Properties.EXPANSION_OUTPUT_VOLTAGE:
                self.state.expansion_output_voltage = float(decoded)
        return b""

    def _on_set_channel(self, args: bytes) -> bytes:
        channel, enabled = struct.unpack_from("<II", args)
        if channel not in self.channels:
            raise self._Fail(ERR_UNKNOWN_PROPERTY)
        (self.state.enabled_channels.add if enabled else self.state.enabled_channels.discard)(channel)
        return b""

    def _on_record_start(self, args: bytes) -> bytes:
        self.state.recording = True
        self.state.next_packet_at = self._clock()
        return b""

    def _on_record_stop(self, args: bytes) -> bytes:
        self.state.recording = False
        return b""

    # ------------------------------------------------------------ sample generation

    def _levels(self) -> dict[int, float]:
        s, c = self.state, self.config
        voltage = s.voltage_setpoint if s.output_enabled else c.off_voltage
        current = min(voltage / c.load_resistance, s.current_limit) if s.output_enabled else 0.0
        return {ChannelId.MV: voltage, ChannelId.MC: current, ChannelId.MP: voltage * current}

    def _emit_samples(self, max_packets: int = 200) -> None:
        s = self.state
        if not s.recording or not s.enabled_channels:
            return
        per_packet = self.config.sample_rate // 1000
        levels = self._levels()
        emitted = 0
        while s.next_packet_at <= self._clock() and emitted < max_packets:
            blocks = []
            for channel in sorted(s.enabled_channels):
                info = self.channels[channel]
                values = np.full(per_packet, levels.get(channel, 0.0))
                if self.config.noise:
                    values += self._rng.normal(0.0, self.config.noise, per_packet)
                blocks.append(SampleBlock(channel, per_packet, info.sample_rate,
                                          info.encode_samples(values)))
            self._send(encode_data_packet(DataPacket(s.sequence, tuple(blocks))))
            s.sequence += 1
            s.next_packet_at += 0.001
            emitted += 1
