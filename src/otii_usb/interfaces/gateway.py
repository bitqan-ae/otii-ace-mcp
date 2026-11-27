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
# under a lock, each on a fresh connection bound to a cancel event
# (DC-ARCH-RUN-001). The
# safe state (outputs off, 5 V, 500 mA) is applied before the first call
# (REQ-SAFE-OUT-001). shutdown() sets the cancel event so a running call aborts
# within 50 ms, waits for it to release the port, restores the safe state
# over a connection that is not bound to the event, and refuses further
# calls (REQ-SAFE-OUT-002, REQ-SAFE-OUT-004).

"""Serialised, cancellable device access with safe-state handling (MCP server, interactive)."""
import logging
import threading
from collections.abc import Callable

from ..device.models import SafeDefaults, SafeStateReport
from ..device.otii import OtiiDevice
from .common import DeviceFactory, device_factories
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
                 cancel: threading.Event | None = None,
                 safe_factory: DeviceFactory | None = None) -> None:
        if cancel is not None and safe_factory is None:
            raise ValueError("a gateway with a cancel event needs a safe_factory whose "
                             "connections are not bound to it")
        self._factory = factory
        self._safe_factory = safe_factory or factory
        self._defaults = defaults
        self._cancel = cancel or threading.Event()
        self._lock = threading.Lock()
        self._safe_applied = False
        self._closed = False
        self.last_output: bool | None = None
        """Main output state seen at the end of the last call (None: unknown)."""

    @classmethod
    def create(cls, port: str | None, *, simulate: bool, defaults: SafeDefaults) -> "DeviceGateway":
        cancel = threading.Event()
        bound, unbound = device_factories(port, simulate=simulate, cancel=cancel)
        return cls(bound, defaults, cancel, safe_factory=unbound)

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
        self.last_output = False
        log.info("safe state applied: %s", report)
        return report

    def call[T](self, action: Callable[[OtiiDevice], T]) -> T:
        with self._lock:
            return self._call_locked(action)

    def try_call[T](self, action: Callable[[OtiiDevice], T]) -> tuple[bool, T | None]:
        """Run ``action`` only if the device is idle; returns (ran, result)."""
        if not self._lock.acquire(blocking=False):
            return False, None
        try:
            return True, self._call_locked(action)
        finally:
            self._lock.release()

    def _call_locked[T](self, action: Callable[[OtiiDevice], T]) -> T:
        if self._closed:
            raise RuntimeError("shutting down")
        with self._factory() as device:
            self._apply_safe(device)
            try:
                return action(device)
            finally:
                self._note_output(device)

    def _note_output(self, device: OtiiDevice) -> None:
        try:
            self.last_output = device.output_enabled
        except Exception:                        # noqa: BLE001 - state unknown, not fatal
            self.last_output = None

    def restore_safe_state(self) -> SafeStateReport | None:
        """Stop recording, outputs off, defaults restored; the gateway stays usable."""
        with self._lock:
            return self._safe_shutdown()

    def _safe_shutdown(self) -> SafeStateReport | None:
        report = safe_shutdown(self._safe_factory, self._defaults)
        self.last_output = False if report is not None else None
        return report

    def shutdown(self) -> SafeStateReport | None:
        """Cancel any running call, then restore the safe state and close the gateway."""
        self._cancel.set()
        acquired = self._lock.acquire(timeout=self.SHUTDOWN_WAIT)
        if not acquired:
            log.warning("a device call did not stop within %.0f s", self.SHUTDOWN_WAIT)
        try:
            self._closed = True      # the cancel event stays set: a late call cannot go on
            return self._safe_shutdown()
        finally:
            if acquired:
                self._lock.release()
