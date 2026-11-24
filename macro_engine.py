"""Serialize macro output without reading Tk widgets from worker threads."""

from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | MAC-01 | publication trace; see NOTICE.md.
import math
import threading
import time
from dataclasses import dataclass


@dataclass
class PeriodicKey:
    key: str
    interval: float
    due_at: float


@dataclass
class _Sequence:
    token: int
    trigger: str
    steps: tuple[tuple[str, float], ...]
    repeat: bool
    interval: float
    periodic: PeriodicKey | None = None
    released: bool = False


class _OutputBlocked(Exception):
    pass


class MacroEngine:
    """Run the latest requested sequence on one persistent worker.

    Each step contains a key and a delay *before* pressing that key. Repeats
    wait ``interval`` seconds after the preceding sequence has finished.

    Releasing a trigger always allows its first sequence to finish, including
    a tap released before the worker starts. It prevents further repeats and
    interrupts a repeat already in progress. A new submission, ``stop()``, or
    ``close()`` cancels every uncommitted step of the old sequence, even its
    first iteration. Entering the dispatch critical section commits a step;
    its ``dd.key_press`` must finish so the corresponding key-up can run.
    ``stop()`` returns False while a committed call is still finishing and
    then reports ``("stopped", "")``. Only True or that completion event
    confirms that stopping has finished. Cancellation never waits indefinitely
    for native code.

    A driver error cancels all work, retries releasing held inputs on this
    worker, and sends ``("error", message)`` to the supplied queue. Further
    submissions are ignored until ``recover()`` retries cleanup on the worker
    and reports ``("recovered", "")``. Stopping alone never clears a fault.
    """

    def __init__(self, dd, events_queue, output_allowed=None) -> None:
        self._dd = dd
        self._events = events_queue
        self._output_allowed = output_allowed
        self._condition = threading.Condition()
        self._dispatch_lock = threading.Lock()
        self._token = 0
        self._held: set[str] = set()
        self._pending: _Sequence | None = None
        self._active: _Sequence | None = None
        self._closed = False
        self._faulted = False
        self._dispatching = False
        self._stopping = False
        self._stop_checks = 0
        self._recovering = False
        self._recovery_requested: int | None = None
        self._worker = threading.Thread(
            target=self._run, name="YanziMacroWorker", daemon=True
        )
        self._worker.start()

    @property
    def ready(self) -> bool:
        """Whether new sequences may be accepted, without waiting on native code."""
        with self._condition:
            return not (self._closed or self._faulted or self._recovering or self._stopping)

    @property
    def idle(self) -> bool:
        """Whether there is no committed output, queued work, or recovery."""
        with self._condition:
            return (not self._dispatching and self._pending is None
                    and self._active is None and not self._recovering)

    def submit(
        self,
        trigger: str,
        steps: tuple[tuple[str, float], ...],
        repeat: bool = False,
        interval: float = 1.2,
        *, periodic: PeriodicKey | None = None,
    ) -> bool:
        """Queue the new sequence and return whether it was accepted."""
        trigger = trigger.strip().lower()
        if not trigger:
            raise ValueError("触发按键不能为空")
        normalized = tuple((key.strip().lower(), float(delay)) for key, delay in steps)
        if not normalized or any(not key for key, _ in normalized):
            raise ValueError("按键序列不能为空，且每一步必须指定按键")
        if any(not math.isfinite(delay) or delay < 0 for _, delay in normalized):
            raise ValueError("按键延迟必须是非负有限数值")
        interval = float(interval)
        if not math.isfinite(interval) or interval < 0 or (repeat and interval == 0):
            raise ValueError("循环间隔必须是有限数值，重复执行时必须大于零")

        with self._condition:
            if self._closed:
                raise RuntimeError("按键执行器已关闭")
            if self._faulted or self._recovering or self._stopping or trigger in self._held:
                return False
            self._held.add(trigger)
            self._token += 1
            self._pending = _Sequence(
                self._token, trigger, normalized, bool(repeat), interval, periodic
            )
            self._condition.notify_all()
            return True

    def release(self, trigger: str) -> None:
        """Finish the first sequence, but cancel subsequent repeats."""
        trigger = trigger.strip().lower()
        with self._condition:
            self._held.discard(trigger)
            for sequence in (self._pending, self._active):
                if sequence is not None and sequence.trigger == trigger:
                    sequence.released = True
            self._condition.notify_all()

    def _cancel_locked(self) -> None:
        self._token += 1
        self._pending = None
        self._recovery_requested = None
        self._recovering = False
        self._held.clear()
        self._condition.notify_all()

    def stop(self, timeout: float = 0.0) -> bool:
        """Cancel immediately; True confirms no committed call is outstanding.

        False is asynchronous completion: wait for ``("stopped", "")`` before
        enabling output again. The default never blocks on a native call.
        """
        timeout = self._checked_timeout(timeout)
        with self._condition:
            self._cancel_locked()
            self._stopping = True
            self._stop_checks += 1
        # Do not hold the condition while acquiring this lock: the dispatcher
        # needs the condition to perform its final cancellation check.
        acquired = self._dispatch_lock.acquire(timeout=timeout)
        if acquired:
            try:
                with self._condition:
                    self._stop_checks -= 1
                    self._stopping = False
                return True
            finally:
                self._dispatch_lock.release()
        with self._condition:
            self._stop_checks -= 1
            # A dispatcher that has not yet passed its final check will see
            # the cancelled token. It cannot send an old step after True.
            if not self._dispatching:
                self._stopping = False
                return True
            self._condition.notify_all()
        return False

    def recover(self) -> bool:
        """Request worker-side input cleanup; report success asynchronously.

        Return False while stopping, so callers can retry after ``stopped``.
        Repeated requests during one recovery are coalesced.
        """
        with self._condition:
            if self._closed:
                raise RuntimeError("按键执行器已关闭")
            if self._stopping:
                return False
            if self._recovering:
                return True
            self._cancel_locked()
            self._faulted = True
            self._recovering = True
            self._recovery_requested = self._token
            self._condition.notify_all()
            return True

    @staticmethod
    def _checked_timeout(timeout: float) -> float:
        timeout = float(timeout)
        if not math.isfinite(timeout) or not 0 <= timeout <= threading.TIMEOUT_MAX:
            raise ValueError("等待时间必须是系统支持范围内的非负有限数值")
        return timeout

    def close(self, timeout: float = 2.0) -> bool:
        """Stop and join the worker; only True permits unloading the driver.

        A False result means a driver call is still running. The caller must
        keep the driver loaded and retry closing later. Once True is returned
        the worker cannot emit any further output. Repeated calls are safe.
        """
        timeout = self._checked_timeout(timeout)
        with self._condition:
            self._closed = True
            self._cancel_locked()
        self._worker.join(timeout)
        return not self._worker.is_alive()

    def _cancelled_locked(self, sequence: _Sequence, first: bool) -> bool:
        return (
            self._closed
            or sequence.token != self._token
            or (not first and sequence.released)
        )

    def _wait(self, sequence: _Sequence, delay: float, first: bool) -> bool:
        """Return False when cancelled, waking immediately on state changes."""
        deadline = time.monotonic() + delay
        with self._condition:
            while not self._cancelled_locked(sequence, first):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                self._condition.wait(min(remaining, threading.TIMEOUT_MAX))
            return False

    def _send_step(self, sequence: _Sequence, key: str, first: bool) -> bool:
        # Yukki / ssyb [MAC-02]: commit under the dispatch lock before native output.
        # This gate is also used to confirm stop completion. Final validation
        # and the complete down/up pair are one committed dispatch operation.
        with self._dispatch_lock:
            with self._condition:
                if self._cancelled_locked(sequence, first):
                    return False
                self._dispatching = True
            try:
                if self._output_allowed is not None and not self._output_allowed():
                    raise _OutputBlocked("目标窗口已切换，宏已停止")
                # A foreground check may itself yield to a cancellation.
                with self._condition:
                    if self._cancelled_locked(sequence, first):
                        return False
                self._dd.key_press(key)
                return True
            except _OutputBlocked:
                raise
            except Exception:
                # Close the acceptance gate before releasing dispatch ownership.
                with self._condition:
                    self._faulted = True
                    self._cancel_locked()
                raise
            finally:
                with self._condition:
                    self._dispatching = False

    def _execute(self, sequence: _Sequence) -> None:
        first = True
        while True:
            periodic = sequence.periodic
            if periodic is not None and time.monotonic() >= periodic.due_at:
                if not self._send_step(sequence, periodic.key, first):
                    return
                periodic.due_at = time.monotonic() + periodic.interval
                if not self._wait(sequence, sequence.interval, first):
                    return
            for key, delay in sequence.steps:
                if not self._wait(sequence, delay, first):
                    return
                if not self._send_step(sequence, key, first):
                    return
            if not sequence.repeat:
                return
            first = False
            if not self._wait(sequence, sequence.interval, first):
                return

    def _release_inputs(self) -> None:
        with self._dispatch_lock:
            with self._condition:
                self._dispatching = True
            try:
                release_all = getattr(self._dd, "release_all", None)
                if release_all is not None:
                    release_all()
            finally:
                with self._condition:
                    self._dispatching = False

    def _recover(self, token: int) -> None:
        try:
            self._release_inputs()
        except Exception as exc:
            with self._condition:
                if self._closed or token != self._token:
                    return
                self._faulted = True
                self._cancel_locked()
                self._events.put(("error", f"释放按键失败：{exc}"))
            return
        with self._condition:
            if self._closed or token != self._token:
                return
            self._faulted = False
            self._recovering = False
            self._events.put(("recovered", ""))

    def _report_failure(self, exc: Exception) -> None:
        with self._condition:
            self._faulted = True
            self._cancel_locked()
        message = str(exc)
        try:
            self._release_inputs()
        except Exception as cleanup_exc:
            message += f"\n释放按键失败：{cleanup_exc}"
        with self._condition:
            # A recovery requested before this failure is delivered must not
            # revive work or overwrite the final cleanup result.
            self._faulted = True
            self._cancel_locked()
            self._events.put(("error", message))

    def _notify_stopped_locked(self) -> None:
        if (self._stopping and not self._stop_checks and not self._dispatching
                and self._pending is None and not self._recovering):
            self._stopping = False
            if not self._closed:
                self._events.put(("stopped", ""))

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and self._recovery_requested is None and not self._closed:
                    self._notify_stopped_locked()
                    self._condition.wait()
                if self._closed:
                    return
                recovery = self._recovery_requested
                self._recovery_requested = None
                if recovery is None:
                    sequence = self._pending
                    self._pending = None
                    self._active = sequence
            try:
                if recovery is not None:
                    self._recover(recovery)
                else:
                    self._execute(sequence)
            except _OutputBlocked as exc:
                with self._condition:
                    self._cancel_locked()
                    self._events.put(("error", str(exc)))
            except Exception as exc:
                self._report_failure(exc)
            finally:
                with self._condition:
                    self._active = None
                    self._notify_stopped_locked()
