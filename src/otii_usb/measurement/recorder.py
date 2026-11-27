# recorder.py: runs a recording and always cleans up
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
# Enables the requested channels, starts recording, lets the session pump
# for the requested time while a router decodes each data packet into
# numpy arrays for the sink, then stops recording and disables the
# channels - in finally blocks, so this also happens on Ctrl-C or errors
# (REQ-SAFE-DEV-005). Counts missing packets from gaps in the sequence numbers.

"""Drives a recording: enable channels, stream into a sink, always clean up."""
import logging
import struct
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from ..errors import OtiiError
from ..protocol.constants import Command, channel_name
from ..protocol.packets import DataPacket, Packet
from ..protocol.session import ProgressCallback, Session
from .recording import Recording
from .sinks import ArraySink, SampleSink

if TYPE_CHECKING:
    from ..device.channels import ChannelInfo, ChannelTable

log = logging.getLogger(__name__)
_CHANNEL_ARGS = struct.Struct("<II")


@dataclass(frozen=True, slots=True)
class StreamResult:
    packets: int
    missing_packets: int


class _PacketRouter:
    """Packet listener: decodes data packets and hands the samples to a sink."""

    def __init__(self, channels: dict[int, "ChannelInfo"], sink: SampleSink) -> None:
        self._channels = channels
        self._sink = sink
        self._last_sequence: int | None = None
        self.closed = False
        self.packets = 0
        self.missing_packets = 0

    def __call__(self, packet: Packet) -> None:
        if self.closed or not isinstance(packet, DataPacket):
            return
        if self._last_sequence is not None and packet.sequence > self._last_sequence + 1:
            self.missing_packets += packet.sequence - self._last_sequence - 1
        self._last_sequence = packet.sequence
        samples: dict[int, NDArray[np.float64]] = {}
        for block in packet.blocks:
            info = self._channels.get(block.channel)
            if info is not None and block.count:
                samples[block.channel] = info.decode_samples(block.data, block.count)
        if samples:
            self.packets += 1
            self._sink.write(packet.sequence, samples)


class Recorder:
    def __init__(self, session: Session, channel_table: "ChannelTable") -> None:
        self._session = session
        self._table = channel_table

    def _resolve(self, channels: Sequence[int]) -> dict[int, "ChannelInfo"]:
        infos: dict[int, ChannelInfo] = {}
        for channel in channels:
            info = self._table.get(channel)
            if info is None or not info.is_analog:
                raise ValueError(f"channel {channel_name(channel)} is not an analog channel "
                                 "on this device")
            infos[channel] = info
        return infos

    def stream(self, channels: Sequence[int], duration: float, sink: SampleSink,
               progress: ProgressCallback | None = None) -> StreamResult:
        """Record ``duration`` seconds into ``sink``; the device is cleaned up on any exit."""
        infos = self._resolve(channels)
        router = _PacketRouter(infos, sink)
        sink.start(infos)
        try:
            with self._session.listening(router), self._streaming(list(infos)):
                try:
                    self._session.pump(duration, progress)
                finally:
                    router.closed = True       # packets during RECORD_STOP are not recorded
        finally:
            sink.finish()
        return StreamResult(router.packets, router.missing_packets)

    def record(self, channels: Sequence[int], duration: float,
               progress: ProgressCallback | None = None) -> Recording:
        sink = ArraySink()
        result = self.stream(channels, duration, sink, progress)
        return sink.to_recording(duration, result.missing_packets)

    @contextmanager
    def _streaming(self, channels: list[int]) -> Iterator[None]:
        enabled: list[int] = []
        try:
            for channel in channels:
                self._session.request(Command.SET_CHANNEL, _CHANNEL_ARGS.pack(channel, 1))
                enabled.append(channel)
            self._session.request(Command.RECORD_START)
            try:
                yield
            finally:
                self._session.request(Command.RECORD_STOP)
        finally:
            for channel in reversed(enabled):
                try:
                    self._session.request(Command.SET_CHANNEL, _CHANNEL_ARGS.pack(channel, 0))
                except OtiiError as e:
                    log.warning("could not disable channel %s: %s", channel_name(channel), e)
