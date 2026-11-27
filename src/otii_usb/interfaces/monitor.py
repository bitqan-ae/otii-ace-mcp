# monitor.py: background polling for overcurrent and unexpected output
# changes, reported as events
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
# The protection itself is done by the device: when the main output current
# reaches the configured current limit, the Otii acts on its own. This
# monitor only observes and reports (REQ-SAFE-MON-001, DC-ARCH-RUN-004). Every
# `interval` seconds, and only while no other call is using the device
# (DeviceGateway.try_call, DC-ARCH-RUN-003), it
# reads the main output state and the current limit and, with the output on,
# samples the current for `window` seconds. It reports:
#   - OVERCURRENT (critical): the peak current reached the limit;
#   - near limit (warning): the peak current exceeded `near_ratio` x limit;
#   - output switched off by the device (critical): the output is off
#     although otii-usb last left it on;
#   - device alerts (critical) received during the poll.
# A condition is reported when it starts, repeated every `repeat` seconds
# while it lasts, and a recovery is reported once. check_summary() applies
# the same limit check to the result of a measurement or recording.

"""Overcurrent and output-state monitoring by polling."""
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..device.otii import OtiiDevice
from ..errors import OtiiError
from ..measurement.stats import MeasurementSummary
from .common import format_si
from .gateway import DeviceGateway

log = logging.getLogger(__name__)

AT_LIMIT_RATIO = 0.98
"""Peak currents at or above this fraction of the limit count as having reached it."""


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    level: str                          # "critical", "warning" or "info"
    kind: str                           # "overcurrent", "near_limit", "output_tripped", "alert", "recovered"
    message: str
    time: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"level": self.level, "kind": self.kind, "message": self.message,
                "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.time)),
                **self.data}


@dataclass(frozen=True, slots=True)
class PollResult:
    output_on: bool
    current_limit: float
    expected_on: bool | None = None
    peak_current: float | None = None
    alerts: tuple[str, ...] = ()


def classify(peak: float, limit: float, near_ratio: float) -> str | None:
    if limit <= 0:
        return None
    if peak >= AT_LIMIT_RATIO * limit:
        return "overcurrent"
    if peak >= near_ratio * limit:
        return "near_limit"
    return None


class OvercurrentMonitor:
    def __init__(self, gateway: DeviceGateway, notify: Callable[[MonitorEvent], None], *,
                 interval: float = 1.0, window: float = 0.02, near_ratio: float = 0.9,
                 repeat: float = 10.0) -> None:
        if interval <= 0 or window <= 0:
            raise ValueError("interval and window must be positive")
        self._gateway = gateway
        self._notify = notify
        self.interval = interval
        self.window = window
        self.near_ratio = near_ratio
        self.repeat = repeat
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: dict[str, float] = {}            # condition -> last reported (monotonic)

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is None and not self._stop.is_set():
            thread = threading.Thread(target=self._run, name="otii-monitor", daemon=True)
            thread.start()
            self._thread = thread

    def stop(self, *, wait: bool = True) -> None:
        """Stop polling; safe to call at any time, also before start().

        With ``wait=False`` it only asks the thread to stop: shutdown paths cancel a running
        poll through the gateway instead of waiting for it (REQ-SAFE-OUT-004).
        """
        self._stop.set()
        thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread() \
                and thread.is_alive():
            thread.join(timeout=2 * self.interval + 1)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll_once()
            except Exception:                           # noqa: BLE001 - keep monitoring
                log.exception("monitor poll failed")

    # ------------------------------------------------------------ polling

    def poll_once(self) -> bool:
        """One poll if the device is idle; returns whether it ran."""
        try:
            ran, result = self._gateway.try_call(self._poll)
        except (OtiiError, RuntimeError) as e:
            log.debug("monitor poll skipped: %s", e)
            return False
        if ran and result is not None:
            self._evaluate(result)
        return ran

    def _poll(self, device: OtiiDevice) -> PollResult:
        expected_on = self._gateway.last_output          # read under the gateway's lock
        on = device.output_enabled
        limit = device.current_limit
        peak = None
        if on:
            current = device.measure(self.window).current
            if current is not None:
                peak = current.maximum
        return PollResult(on, limit, expected_on, peak,
                          tuple(a.data.hex(" ") for a in device.alerts))

    def _evaluate(self, poll: PollResult) -> None:
        for alert in poll.alerts:
            self._emit(MonitorEvent("critical", "alert", f"device alert: {alert}",
                                    data={"alert": alert}))
        if poll.expected_on and not poll.output_on:
            self._emit(MonitorEvent(
                "critical", "output_tripped",
                "main output was switched OFF by the device, not by otii-usb "
                "(current limit or protection tripped?)"))
        condition = None
        if poll.peak_current is not None:
            condition = classify(poll.peak_current, poll.current_limit, self.near_ratio)
            if condition is not None:
                self._report(condition, poll.peak_current, poll.current_limit, "polled")
        for kind in ("overcurrent", "near_limit"):
            if kind != condition and kind in self._active:
                del self._active[kind]
                if kind == "overcurrent" and poll.output_on:
                    self._emit(MonitorEvent("info", "recovered",
                                            "current back below the limit"))

    def check_summary(self, summary: MeasurementSummary | None, limit: float) -> None:
        """Apply the limit check to a measurement or recording that just finished."""
        if summary is None or summary.current is None:
            return
        condition = classify(summary.current.maximum, limit, self.near_ratio)
        if condition is not None:
            self._report(condition, summary.current.maximum, limit, "during the recording",
                         force=True)

    def _report(self, condition: str, peak: float, limit: float, where: str, *,
                force: bool = False) -> None:
        now = time.monotonic()
        last = self._active.get(condition)
        if not force and last is not None and now - last < self.repeat:
            return
        self._active[condition] = now
        data = {"peak_current_a": peak, "current_limit_a": limit}
        if condition == "overcurrent":
            self._emit(MonitorEvent(
                "critical", "overcurrent",
                f"OVERCURRENT: peak {format_si(peak, 'A')} reached the current limit "
                f"{format_si(limit, 'A')} ({where})", data=data))
        else:
            self._emit(MonitorEvent(
                "warning", "near_limit",
                f"current near the limit: peak {format_si(peak, 'A')} "
                f"({peak / limit:.0%} of {format_si(limit, 'A')}, {where})", data=data))

    def _emit(self, event: MonitorEvent) -> None:
        log.log(logging.WARNING if event.level != "info" else logging.INFO, "%s", event.message)
        try:
            self._notify(event)
        except Exception:                               # noqa: BLE001 - never kill the monitor
            log.exception("monitor notification failed")


class EventQueue:
    """Thread-safe buffer of monitor events, drained into the next MCP tool result."""

    def __init__(self, maxlen: int = 50) -> None:
        self._events: list[MonitorEvent] = []
        self._maxlen = maxlen
        self._lock = threading.Lock()

    def push(self, event: MonitorEvent) -> None:
        with self._lock:
            self._events.append(event)
            del self._events[:-self._maxlen]

    def drain(self) -> list[dict]:
        with self._lock:
            events, self._events = self._events, []
        return [e.to_dict() for e in events]
