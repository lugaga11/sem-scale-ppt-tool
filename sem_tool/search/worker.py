from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, cast

from PySide6.QtCore import QObject, Signal, Slot

from .index import ImageIndex, IndexStatus, ProgressCallback

Task = Literal["update", "rebuild", "search", "status", "clear", "prune", "vacuum"]


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


@dataclass(frozen=True)
class SearchBlocked:
    """The check before a search found nothing searchable, or an index that no longer matches the gallery."""

    status: IndexStatus


class SearchWorker(QObject):
    """Run one index or search task off the interface thread.

    Everything that scans the gallery or touches the database goes through here, including the
    status shown under the gallery field; ``status_ready`` reports it whenever a task computed one.
    """

    progress = Signal(int, int, str)
    status_ready = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    canceled = Signal()
    done = Signal()

    def __init__(
        self,
        root: Path | None,
        cancel_event: threading.Event,
        task: Task = "update",
        *,
        query: Path | None = None,
        top_n: int = 10,
        precheck: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.cancel_event = cancel_event
        self.task = task
        self.query = query
        self.top_n = top_n
        self.precheck = precheck

    @Slot()
    def run(self) -> None:
        throttler = ProgressThrottler(self.progress.emit)
        try:
            result = self._execute(ImageIndex(), throttler.emit)
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

    def _execute(self, index: ImageIndex, progress: ProgressCallback) -> object:
        cancel = self.cancel_event
        if self.task == "vacuum":  # works on the whole database file, not on one gallery
            before = index.db_path.stat().st_size
            index.vacuum()
            return before, index.db_path.stat().st_size

        root = cast(Path, self.root)
        if self.task == "search":
            if self.precheck:
                status = index.status(root, cancel_event=cancel)
                if cancel.is_set():
                    return None
                self.status_ready.emit(status)
                if status.searchable_files == 0 or status.needs_update:
                    return SearchBlocked(status)
            return index.search(root, cast(Path, self.query), top_n=self.top_n, progress=progress, cancel_event=cancel)

        result: object = None
        if self.task == "update":
            result = index.update(root, progress=progress, cancel_event=cancel)
        elif self.task == "rebuild":
            result = index.rebuild(root, progress=progress, cancel_event=cancel)
        elif self.task == "clear":
            result = index.clear(root)
        elif self.task == "prune":
            result = index.prune_missing(root, cancel_event=cancel)
        status = index.status(root, cancel_event=cancel)
        if not cancel.is_set():
            self.status_ready.emit(status)
        return status if self.task == "status" else result
