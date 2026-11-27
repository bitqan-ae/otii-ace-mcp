# gateway.py: serialised, cancellable access to one device with
# safe-state handling
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
# Used by the MCP server and the interactive shell. Calls run one at a time
# under a lock, each on a fresh connection bound to a cancel event. The
# safe state (outputs off, 5 V, 500 mA) is applied before the first call
# (SAF-001). shutdown() sets the cancel event so a running call aborts
# within 50 ms, waits for it to release the port, restores the safe state
# and refuses further calls (SAF-002, SAF-004).

"""Serialised, cancellable device access with safe-state handling (MCP server, interactive)."""
import logging
import threading
from collections.abc import Callable

from ..device.models import SafeDefaults, SafeStateReport
from ..device.otii import OtiiDevice
from .common import DeviceFactory, device_factory
from .lifecycle import safe_shutdown

log = logging.getLogger(__name__)


class DeviceGateway:
    """One device, many callers.

    * Calls are serialised; each opens a fresh connection (the port is free in between).
    * The safe state (:class:`SafeDefaults`) is applied before the first call.
    * :meth:`shutdown` cancels a running call through the cancel event the factory's
      connections are bound to, then restores the safe state and refuses further calls.
    """

    SHUTDOWN_WAIT = 5.0

    def __init__(self, factory: DeviceFactory, defaults: SafeDefaults,
                 cancel: threading.Event | None = None) -> None:
        self._factory = factory
        self._defaults = defaults
        self._cancel = cancel or threading.Event()
        self._lock = threading.Lock()
        self._safe_applied = False
        self._closed = False

    @classmethod
    def create(cls, port: str | None, *, simulate: bool, defaults: SafeDefaults) -> "DeviceGateway":
        cancel = threading.Event()
        return cls(device_factory(port, simulate=simulate, cancel=cancel), defaults, cancel)

    @property
    def defaults(self) -> SafeDefaults:
        return self._defaults

    def ensure_safe(self) -> dict | None:
        """Apply the safe state now unless it was applied already; returns the report."""
        with self._lock:
            if self._closed:
                return None
            with self._factory() as device:
                return self._apply_safe(device)

    def _apply_safe(self, device: OtiiDevice) -> dict | None:
        if self._safe_applied:
            return None
        report = device.apply_safe_defaults(self._defaults).to_dict()
        self._safe_applied = True
        log.info("safe state applied: %s", report)
        return report

    def call[T](self, action: Callable[[OtiiDevice], T]) -> T:
        with self._lock:
            if self._closed:
                raise RuntimeError("shutting down")
            with self._factory() as device:
                self._apply_safe(device)
                return action(device)

    def restore_safe_state(self) -> SafeStateReport | None:
        """Stop recording, outputs off, defaults restored; the gateway stays usable."""
        with self._lock:
            return safe_shutdown(self._factory, self._defaults)

    def shutdown(self) -> SafeStateReport | None:
        """Cancel any running call, then restore the safe state and close the gateway."""
        self._cancel.set()
        acquired = self._lock.acquire(timeout=self.SHUTDOWN_WAIT)
        if not acquired:
            log.warning("a device call did not stop within %.0f s", self.SHUTDOWN_WAIT)
        try:
            self._closed = True
            self._cancel.clear()
            return safe_shutdown(self._factory, self._defaults)
        finally:
            if acquired:
                self._lock.release()
