# conftest.py: shared fixtures for the test suite
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
# A simulator with a reduced sample rate (fast tests), a connected device,
# and SilentTransport, a device that never answers (timeouts, handshake
# fallback). --hardware runs tests/hardware; tests there that switch the
# output (marked switches_output) also need --switch-output.

from pathlib import Path

import pytest

from otii_usb.device import OtiiDevice
from otii_usb.simulator import SimulatedOtii, SimulationConfig
from otii_usb.transport import Transport


class SilentTransport(Transport):
    """A device that never answers."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    def read(self, timeout: float) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def simulator() -> SimulatedOtii:
    return SimulatedOtii(SimulationConfig(sample_rate=5_000))


@pytest.fixture
def device(simulator: SimulatedOtii):
    with OtiiDevice.connect(simulator) as dev:
        yield dev


HARDWARE_DIR = Path(__file__).parent / "hardware"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--hardware", action="store_true",
                     help="also run tests/hardware against an attached Otii Ace Pro")
    parser.addoption("--switch-output", action="store_true",
                     help="with --hardware: also run tests that switch the main output on")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_hardware = config.getoption("--hardware")
    switch_output = config.getoption("--switch-output")
    skip = pytest.mark.skip(reason="needs an Otii Ace Pro; run with --hardware")
    skip_power = pytest.mark.skip(reason="powers the device under test; add --switch-output")
    for item in items:
        if HARDWARE_DIR in Path(item.path).parents:
            item.add_marker("hardware")
            if not run_hardware:
                item.add_marker(skip)
            elif "switches_output" in item.keywords and not switch_output:
                item.add_marker(skip_power)
