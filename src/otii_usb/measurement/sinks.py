# sinks.py: where streamed samples go
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
# SampleSink is the interface: start() with the channel table, write() once
# per 1 ms packet with numpy arrays per channel, finish() at the end (also
# after an interruption). ArraySink builds an in-memory Recording, TeeSink
# feeds several sinks from one recording (CSV and plot in parallel);
# CsvSink and PlotSink live with their formats (DC-ARCH-PKG-006).

"""Destinations for streamed sample data."""
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from .recording import Recording

if TYPE_CHECKING:
    from ..device.channels import ChannelInfo


class SampleSink(ABC):
    """Receives decoded samples packet by packet while a recording runs."""

    def start(self, channels: Mapping[int, "ChannelInfo"]) -> None:
        """Called once before the first packet with the channels being recorded."""

    @abstractmethod
    def write(self, sequence: int, samples: Mapping[int, NDArray[np.float64]]) -> None:
        """One data packet (1 ms): SI-unit samples per channel; ``sequence`` counts packets."""

    def finish(self) -> None:
        """Called once after the last packet (also when the recording was interrupted)."""


class ArraySink(SampleSink):
    """Keeps everything in memory and builds a :class:`Recording`."""

    def __init__(self) -> None:
        self._chunks: dict[int, list[NDArray[np.float64]]] = {}
        self._rates: dict[int, int] = {}

    def start(self, channels: Mapping[int, "ChannelInfo"]) -> None:
        self._chunks = {ch: [] for ch in channels}
        self._rates = {ch: info.sample_rate for ch, info in channels.items()}

    def write(self, sequence: int, samples: Mapping[int, NDArray[np.float64]]) -> None:
        for channel, values in samples.items():
            self._chunks[channel].append(values)

    def to_recording(self, duration: float, missing_packets: int = 0) -> Recording:
        samples = {ch: np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float64)
                   for ch, chunks in self._chunks.items()}
        return Recording(duration, samples, dict(self._rates), missing_packets)


class TeeSink(SampleSink):
    """Feeds one recording into several sinks (e.g. CSV and plot in parallel)."""

    def __init__(self, *sinks: SampleSink) -> None:
        self.sinks = sinks

    def start(self, channels: Mapping[int, "ChannelInfo"]) -> None:
        for sink in self.sinks:
            sink.start(channels)

    def write(self, sequence: int, samples: Mapping[int, NDArray[np.float64]]) -> None:
        for sink in self.sinks:
            sink.write(sequence, samples)

    def finish(self) -> None:
        errors = []
        for sink in self.sinks:                 # every sink gets to close its file
            try:
                sink.finish()
            except Exception as e:              # noqa: BLE001 - re-raised below
                errors.append(e)
        if errors:
            raise errors[0]
