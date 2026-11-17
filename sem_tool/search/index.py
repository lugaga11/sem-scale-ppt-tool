from __future__ import annotations

import os
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from ..files import iter_image_files
from ..settings import default_db_path
from .engine import (
    SearchResult,
    deserialize_features,
    extract_features,
    make_thumbnail,
    match_features,
    serialize_features,
)

ProgressCallback = Callable[[int, int, str], None]
# One image stores up to ~100 KB of features, so they are read and matched a chunk at a time.
FEATURE_CHUNK_ROWS = 64


@dataclass(frozen=True)
class IndexStats:
    total: int
    indexed: int
    skipped: int
    removed: int
    errors: int


@dataclass(frozen=True)
class IndexStatus:
    total_files: int
    indexed_files: int
    new_files: int
    changed_files: int
    removed_files: int
    error_files: int
    searchable_files: int
    last_indexed_at: Optional[float]

    @property
    def needs_update(self) -> bool:
        return (
            self.new_files > 0
            or self.changed_files > 0
            or self.removed_files > 0
            or self.error_files > 0
            or self.indexed_files < self.total_files
        )


@dataclass(frozen=True)
class IndexJob:
    path: Path
    path_text: str
    suffix: str
    size: int
    mtime: float


@dataclass(frozen=True)
class IndexResult:
    job: IndexJob
    width: int
    height: int
    keypoint_count: int
    keypoints: bytes
    descriptors: Optional[bytes]
    error: Optional[str]


class ImageIndex:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def update(
        self,
        root: Path,
        *,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        max_workers: Optional[int] = None,
    ) -> IndexStats:
        root = root.resolve()
        files = list(scan_gallery(root, cancel_event))
        if cancel_event and cancel_event.is_set():
            # A listing cut short must not be read as "the remaining files were deleted".
            return IndexStats(total=0, indexed=0, skipped=0, removed=0, errors=0)
        now = time.time()
        indexed = 0
        skipped = 0
        errors = 0

        with closing(self._connect()) as conn:
            existing = {
                row["path"]: row
                for row in conn.execute(
                    "select path, size, mtime, error from images where root = ?",
                    (str(root),),
                )
            }
            seen = {str(path) for path, _stat in files}
            removed = 0
            for path_text in set(existing) - seen:
                conn.execute(
                    "delete from images where root = ? and path = ?",
                    (str(root), path_text),
                )
                removed += 1

            total = len(files)
            processed = 0
            jobs: list[IndexJob] = []
            for path, stat in files:
                if cancel_event and cancel_event.is_set():
                    break
                path_text = str(path)
                old = existing.get(path_text)
                if (
                    old
                    and not old["error"]
                    and old["size"] == stat.st_size
                    and old["mtime"] == stat.st_mtime
                ):
                    processed += 1
                    skipped += 1
                    if progress:
                        progress(processed, total, f"跳过未变化：{path.name}")
                    continue
                jobs.append(
                    IndexJob(
                        path=path,
                        path_text=path_text,
                        suffix=path.suffix.lower(),
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )

            for result in extract_index_results(
                jobs,
                max_workers=max_workers,
                cancel_event=cancel_event,
            ):
                processed += 1
                write_index_result(conn, root, result, now)
                if result.error:
                    errors += 1
                else:
                    indexed += 1
                if processed % 25 == 0:
                    conn.commit()
                if progress:
                    progress(processed, total, f"索引：{result.job.path.name}")
            conn.commit()

        return IndexStats(total=total, indexed=indexed, skipped=skipped, removed=removed, errors=errors)

    def rebuild(
        self,
        root: Path,
        *,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        max_workers: Optional[int] = None,
    ) -> IndexStats:
        # With the old rows gone every file is new to update(), so nothing is skipped.
        removed = self.clear(root)
        stats = self.update(root, progress=progress, cancel_event=cancel_event, max_workers=max_workers)
        return replace(stats, removed=removed)

    def clear(self, root: Path) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute("delete from images where root = ?", (str(root.resolve()),))
            conn.commit()
            return cursor.rowcount

    def prune_missing(self, root: Path, *, cancel_event: Optional[threading.Event] = None) -> int:
        root_text = str(root.resolve())
        with closing(self._connect()) as conn:
            paths = [row["path"] for row in conn.execute("select path from images where root = ?", (root_text,))]
            removed = 0
            for path_text in paths:
                if cancel_event and cancel_event.is_set():
                    break
                if not Path(path_text).exists():
                    conn.execute("delete from images where root = ? and path = ?", (root_text, path_text))
                    removed += 1
            conn.commit()
            return removed

    def vacuum(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("vacuum")

    def search(
        self,
        root: Path,
        query_path: Path,
        *,
        top_n: int = 10,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[SearchResult]:
        root = root.resolve()
        query_features = extract_features(query_path.resolve(), query=True)
        total = self.count(root)
        scored: list[SearchResult] = []

        for current, row in enumerate(self._iter_feature_rows(root), start=1):
            if cancel_event and cancel_event.is_set():
                return []
            path = Path(row["path"])
            if progress:
                progress(current, total, f"匹配：{path.name}")
            candidate = deserialize_features(
                path,
                row["width"],
                row["height"],
                row["keypoints"],
                row["descriptors"],
            )
            match = match_features(query_features, candidate)
            scored.append(
                SearchResult(
                    path=path,
                    score=match.score,
                    inliers=match.inliers,
                    good_matches=match.good_matches,
                    avg_distance=match.avg_distance,
                )
            )

        scored.sort(key=lambda item: (-item.inliers, -item.good_matches, item.avg_distance, str(item.path)))
        results: list[SearchResult] = []
        for result in scored:
            if len(results) == top_n:
                break
            # An original may have been moved since the last index update; never offer a dead path.
            if result.path.is_file():
                results.append(replace(result, thumbnail=make_thumbnail(result.path)))
        return results

    def count(self, root: Path) -> int:
        root = root.resolve()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select count(*) as count from images where root = ? and descriptors is not null",
                (str(root),),
            ).fetchone()
            return int(row["count"])

    def status(self, root: Path, *, cancel_event: Optional[threading.Event] = None) -> IndexStatus:
        """Compare the gallery on disk with its index rows.

        The scan stops as soon as the event is set, so a result obtained after cancelling is incomplete.
        """
        root = root.resolve()
        current = {str(path): stat for path, stat in scan_gallery(root, cancel_event)}
        with closing(self._connect()) as conn:
            rows = {
                row["path"]: row
                for row in conn.execute(
                    """
                    select path, size, mtime, updated_at, error, descriptors is not null as searchable
                    from images
                    where root = ?
                    """,
                    (str(root),),
                )
            }

        current_paths = set(current)
        indexed_paths = set(rows)
        changed_files = 0
        error_files = 0
        indexed_files = 0
        for path_text in current_paths & indexed_paths:
            stat = current[path_text]
            row = rows[path_text]
            if row["error"]:
                error_files += 1
            if row["size"] != stat.st_size or row["mtime"] != stat.st_mtime:
                changed_files += 1
            elif not row["error"]:
                indexed_files += 1

        return IndexStatus(
            total_files=len(current),
            indexed_files=indexed_files,
            new_files=len(current_paths - indexed_paths),
            changed_files=changed_files,
            removed_files=len(indexed_paths - current_paths),
            error_files=error_files,
            searchable_files=sum(1 for row in rows.values() if row["searchable"]),
            last_indexed_at=max((row["updated_at"] for row in rows.values()), default=None),
        )

    def _iter_feature_rows(self, root: Path) -> Iterator[sqlite3.Row]:
        """Yield the searchable rows of a gallery in rowid order, one bounded chunk per query.

        Loading every row at once needs the whole gallery's features in memory, and a single long
        cursor would hold a read lock for the entire search; short chunk queries avoid both.
        """
        last_rowid = 0
        with closing(self._connect()) as conn:
            while True:
                rows = conn.execute(
                    """
                    select rowid, path, width, height, keypoints, descriptors
                    from images not indexed
                    where root = ? and descriptors is not null and rowid > ?
                    order by rowid
                    limit ?
                    """,
                    (str(root), last_rowid, FEATURE_CHUNK_ROWS),
                ).fetchall()
                if not rows:
                    return
                yield from rows
                last_rowid = rows[-1]["rowid"]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                create table if not exists images (
                    root text not null,
                    path text not null,
                    suffix text not null,
                    size integer not null,
                    mtime real not null,
                    width integer not null,
                    height integer not null,
                    keypoint_count integer not null,
                    keypoints blob not null,
                    descriptors blob,
                    updated_at real not null,
                    error text,
                    primary key (root, path)
                )
                """
            )
            conn.commit()


def scan_gallery(
    root: Path, cancel_event: Optional[threading.Event] = None
) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield each image under the gallery as (resolved path, stat).

    The listing ends early once the event is set; callers check the event before trusting it.
    """
    for path in iter_image_files(root):
        if cancel_event and cancel_event.is_set():
            return
        resolved = path.resolve()
        try:
            stat = resolved.stat()
        except FileNotFoundError:
            continue  # removed between listing and stat, e.g. a temporary file of the acquisition software
        yield resolved, stat


def serialize_empty_keypoints() -> bytes:
    from io import BytesIO

    import numpy as np

    buffer = BytesIO()
    np.save(buffer, np.empty((0, 2), dtype=np.float32), allow_pickle=False)
    return buffer.getvalue()


def default_max_workers() -> int:
    return max(1, min(8, os.cpu_count() or 4))


def extract_index_results(
    jobs: Iterable[IndexJob],
    *,
    max_workers: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[IndexResult]:
    """Extract features on a thread pool, yielding results in completion order.

    Only a small window of jobs is submitted at a time and a future is dropped as soon as its
    result is handed over, so indexing a large gallery does not keep every result in memory.
    """
    workers = max_workers or default_max_workers()
    remaining = iter(jobs)
    running: set[Future[IndexResult]] = set()
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        while not (cancel_event and cancel_event.is_set()):
            for job in islice(remaining, workers * 2 - len(running)):
                running.add(executor.submit(extract_index_job, job))
            if not running:
                return
            done, running = wait(running, return_when=FIRST_COMPLETED)
            while done:
                yield done.pop().result()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def extract_index_job(job: IndexJob) -> IndexResult:
    try:
        features = extract_features(job.path)
        keypoints_blob, descriptors_blob = serialize_features(features)
        return IndexResult(
            job=job,
            width=features.width,
            height=features.height,
            keypoint_count=features.keypoint_count,
            keypoints=keypoints_blob,
            descriptors=descriptors_blob,
            error=None,
        )
    except Exception as exc:
        return IndexResult(
            job=job,
            width=0,
            height=0,
            keypoint_count=0,
            keypoints=serialize_empty_keypoints(),
            descriptors=None,
            error=str(exc),
        )


def write_index_result(
    conn: sqlite3.Connection,
    root: Path,
    result: IndexResult,
    updated_at: float,
) -> None:
    conn.execute(
        """
        insert into images (
            root, path, suffix, size, mtime, width, height,
            keypoint_count, keypoints, descriptors, updated_at, error
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(root, path) do update set
            suffix = excluded.suffix,
            size = excluded.size,
            mtime = excluded.mtime,
            width = excluded.width,
            height = excluded.height,
            keypoint_count = excluded.keypoint_count,
            keypoints = excluded.keypoints,
            descriptors = excluded.descriptors,
            updated_at = excluded.updated_at,
            error = excluded.error
        """,
        (
            str(root),
            result.job.path_text,
            result.job.suffix,
            result.job.size,
            result.job.mtime,
            result.width,
            result.height,
            result.keypoint_count,
            result.keypoints,
            result.descriptors,
            updated_at,
            result.error,
        ),
    )
