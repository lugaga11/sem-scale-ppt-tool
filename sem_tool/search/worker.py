from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from .index import ImageIndex


class ProgressThrottler:
    def __init__(self, emit, *, interval_seconds: float = 0.1, step_interval: int = 50) -> None:
        self.emit_signal = emit
        self.interval_seconds = interval_seconds
        self.step_interval = step_interval
        self.last_emit_time = 0.0
        self.last_emit_current: Optional[int] = None
        self.pending: Optional[tuple[int, int, str]] = None

    def emit(self, current: int, total: int, message: str) -> None:
        self.pending = (current, total, message)
        if self._should_emit(current, total):
            self.flush()

    def flush(self) -> None:
        if self.pending is None:
            return
        current, total, message = self.pending
        self.emit_signal(current, total, message)
        self.last_emit_time = time.monotonic()
        self.last_emit_current = current
        self.pending = None

    def _should_emit(self, current: int, total: int) -> bool:
        if self.last_emit_current is None:
            return True
        if total > 0 and current >= total:
            return True
        if current <= 1:
            return True
        if current - self.last_emit_current >= self.step_interval:
            return True
        return time.monotonic() - self.last_emit_time >= self.interval_seconds


class SearchWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    canceled = Signal()
    done = Signal()

    def __init__(
        self,
        root: Path,
        cancel_event: threading.Event,
        *,
        query: Path | None = None,
        top_n: int = 10,
        rebuild: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.cancel_event = cancel_event
        self.query = query
        self.top_n = top_n
        self.rebuild = rebuild

    @Slot()
    def run(self) -> None:
        throttler = ProgressThrottler(self.progress.emit)
        try:
            index = ImageIndex()
            if self.query is not None:
                result = index.search(
                    self.root, self.query, top_n=self.top_n, progress=throttler.emit, cancel_event=self.cancel_event
                )
            else:
                operation = index.rebuild if self.rebuild else index.update
                result = operation(self.root, progress=throttler.emit, cancel_event=self.cancel_event)
            throttler.flush()
            if self.cancel_event.is_set():
                self.canceled.emit()
            else:
                self.finished.emit(result)
        except Exception as exc:
            throttler.flush()
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
