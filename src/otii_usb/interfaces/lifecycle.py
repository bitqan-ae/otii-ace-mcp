# lifecycle.py: signals and the safe shutdown sequence
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
# Ctrl-C, SIGTERM and SIGHUP end the program the same way (REQ-SAFE-OUT-007):
#   - termination_as_interrupt() turns the first of them into a
#     KeyboardInterrupt for code running in the main thread (the one-shot
#     command line) and ignores the rest, also arming SIGINT if it was
#     inherited as ignored (DC-HOST-SIG-001);
#   - SafeExit runs the shutdown on its own thread and then exits the
#     process, for programs whose main thread may be blocked (the MCP
#     server waiting on stdin, the interactive prompt) (DC-ARCH-RUN-002,
#     DC-HOST-SIG-002);
#   - signals_shielded() keeps further signals from interrupting the
#     shutdown (REQ-SAFE-OUT-003);
#   - safe_shutdown() opens a new connection and stops recording, switches
#     the outputs off and restores the defaults, retrying while the port is
#     still held by an aborted call (REQ-SAFE-OUT-002).

"""Process lifecycle: termination signals and the safe shutdown sequence.

Ctrl-C (SIGINT), SIGTERM and SIGHUP all end the program the same way: the current operation
is interrupted, then a fresh connection is opened and the device is put into the safe state
(recording stopped, all outputs off, startup defaults restored). Further signals are ignored
while that cleanup runs, so a second Ctrl-C cannot leave the output on.
"""
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import NoReturn

from ..device.models import SafeDefaults, SafeStateReport
from ..errors import OtiiError
from .common import DeviceFactory

log = logging.getLogger(__name__)

TERMINATION_SIGNALS: tuple[signal.Signals, ...] = tuple(
    s for s in (signal.SIGTERM, getattr(signal, "SIGHUP", None)) if s is not None)
_SHIELDED_SIGNALS = (signal.SIGINT, *TERMINATION_SIGNALS)


def _is_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


@contextmanager
def termination_as_interrupt() -> Iterator[None]:
    """Turn the first SIGINT, SIGTERM or SIGHUP into a KeyboardInterrupt.

    From that first signal on, all three are ignored, so the safe shutdown that handles the
    KeyboardInterrupt cannot be interrupted (REQ-SAFE-OUT-003); the handler stays installed
    (and does nothing) after the block in that case. SIGINT is armed explicitly because processes started in
    the background by a shell inherit it as ignored (DC-HOST-SIG-001).

    The handler does no I/O and takes no lock: a signal can arrive while the main thread is
    in the middle of writing a log line, and a nested write would fail and lose the
    interrupt.
    """
    if not _is_main_thread():
        yield
        return
    fired = [False]

    def interrupt(signum: int, frame: object) -> None:
        if fired[0]:
            return                       # already shutting down: ignore
        fired[0] = True
        raise KeyboardInterrupt

    previous = {s: signal.signal(s, interrupt) for s in _SHIELDED_SIGNALS}
    try:
        yield
    finally:
        if not fired[0]:
            for s, handler in previous.items():
                signal.signal(s, handler)


def _ignore(signum: int, frame: object) -> None:
    """A handler that does nothing.

    Used instead of SIG_IGN: replacing a handler by SIG_IGN while a signal is pending makes
    CPython report "signal ignored due to race condition".
    """


@contextmanager
def signals_shielded() -> Iterator[None]:
    """Ignore SIGINT/SIGTERM/SIGHUP inside the block (used for the shutdown sequence)."""
    if not _is_main_thread():
        yield
        return
    previous = {s: signal.signal(s, _ignore) for s in _SHIELDED_SIGNALS}
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def safe_shutdown(factory: DeviceFactory, defaults: SafeDefaults = SafeDefaults(), *,
                  attempts: int = 5, retry_delay: float = 0.5) -> SafeStateReport | None:
    """Open a new connection and apply the safe state, retrying while the port is busy.

    Returns the report, or ``None`` if the device could not be reached (logged as an error).
    """
    error: OtiiError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with factory() as device:
                report = device.shutdown(defaults)
            log.info("shutdown: outputs off, %s", report.to_dict())
            return report
        except OtiiError as e:      # includes PortBusyError while an aborted call lets go
            error = e
            log.debug("safe shutdown attempt %d/%d failed: %s", attempt, attempts, e)
            if attempt < attempts:
                time.sleep(retry_delay)
    log.error("could not switch outputs off at shutdown: %s", error)
    return None


class SafeExit:
    """Signal-driven shutdown that does not depend on the main thread unwinding.

    install() starts a worker thread that waits. The first SIGINT/SIGTERM/SIGHUP wakes it; it
    runs ``shutdown`` (which must cancel in-flight device calls and switch the outputs off)
    and then ends the process with ``os._exit``. Further signals are ignored. This works
    even while the main thread is blocked (an event loop waiting on a thread that reads
    stdin, DC-HOST-SIG-002).

    The signal handler only sets a flag and an event: it does no I/O and starts no thread,
    because the signal may interrupt the main thread inside a write or while it holds
    the thread registry's lock.
    """

    def __init__(self, shutdown: Callable[[], object | None], *,
                 signals: tuple[signal.Signals, ...] = _SHIELDED_SIGNALS,
                 before_exit: Callable[[], None] | None = None) -> None:
        self._shutdown = shutdown
        self._signals = signals
        self._before_exit = before_exit
        self._fired = False
        self._signal_name = ""
        self._wake = threading.Event()
        self._previous: dict[int, object] = {}

    @property
    def triggered(self) -> bool:
        return self._fired

    def install(self) -> None:
        threading.Thread(target=self._worker, name="safe-exit", daemon=True).start()
        for sig in self._signals:
            self._previous[sig] = signal.signal(sig, self._on_signal)

    def uninstall(self) -> None:
        """Restore the previous handlers (used when the program ends by itself)."""
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        self._previous.clear()

    def _on_signal(self, signum: int, frame: object) -> None:
        if self._fired:
            return                                  # cleanup already running: ignore
        self._fired = True
        self._signal_name = signal.Signals(signum).name
        self._wake.set()

    def _worker(self) -> None:
        self._wake.wait()
        log.info("received %s, switching outputs off", self._signal_name)
        self._finish()

    def _finish(self) -> None:
        try:
            ok = self._shutdown() is not None
        except BaseException:                       # noqa: BLE001 - must always exit
            log.exception("shutdown failed")
            ok = False
        if self._before_exit is not None:
            try:
                self._before_exit()
            except Exception:                       # noqa: BLE001 - exiting anyway
                pass
        logging.shutdown()
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass
        os._exit(0 if ok else 1)

    def exit_now(self) -> NoReturn:
        """Normal end of the program: run the shutdown here (signals ignored) and exit."""
        if self._fired:
            threading.Event().wait()                # the worker thread ends the process
        self._fired = True
        self._finish()
        raise AssertionError("unreachable")
