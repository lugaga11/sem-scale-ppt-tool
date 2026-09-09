from __future__ import annotations

import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Optional

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
        force: bool = False,
        max_workers: Optional[int] = None,
    ) -> IndexStats:
        root = root.resolve()
        files = list(iter_image_files(root))
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
            seen = {str(path.resolve()) for path in files}
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
            for path in files:
                if cancel_event and cancel_event.is_set():
                    break
                path = path.resolve()
                stat = path.stat()
                path_text = str(path)
                old = existing.get(path_text)
                if (
                    not force
                    and old
                    and not old["error"]
                    and old["size"] == stat.st_size
                    and old["mtime"] == stat.st_mtime
                ):
                    processed += 1
                    skipped += 1
                    if progress:
                        progress(processed, total, f"跳过未变化: {path.name}")
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
                    progress(processed, total, f"索引: {result.job.path.name}")
            conn.commit()

        return IndexStats(
            total=len(files),
            indexed=indexed,
            skipped=skipped,
            removed=removed,
            errors=errors,
        )

    def rebuild(
        self,
        root: Path,
        *,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        max_workers: Optional[int] = None,
    ) -> IndexStats:
        removed = self.clear(root)
        stats = self.update(
            root,
            progress=progress,
            cancel_event=cancel_event,
            force=True,
            max_workers=max_workers,
        )
        return IndexStats(
            total=stats.total,
            indexed=stats.indexed,
            skipped=stats.skipped,
            removed=removed + stats.removed,
            errors=stats.errors,
        )

    def clear(self, root: Optional[Path] = None) -> int:
        with closing(self._connect()) as conn:
            if root is None:
                cursor = conn.execute("delete from images")
            else:
                cursor = conn.execute(
                    "delete from images where root = ?",
                    (str(root.resolve()),),
                )
            conn.commit()
            return cursor.rowcount

    def prune_missing(self, root: Optional[Path] = None) -> int:
        with closing(self._connect()) as conn:
            if root is None:
                rows = list(conn.execute("select root, path from images"))
            else:
                rows = list(
                    conn.execute(
                        "select root, path from images where root = ?",
                        (str(root.resolve()),),
                    )
                )
            removed = 0
            for row in rows:
                path = Path(row["path"])
                if path.exists():
                    continue
                conn.execute(
                    "delete from images where root = ? and path = ?",
                    (row["root"], row["path"]),
                )
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
        rows = self._feature_rows(root)
        total = len(rows)
        scored: list[SearchResult] = []

        for current, row in enumerate(rows, start=1):
            if cancel_event and cancel_event.is_set():
                break
            path = Path(row["path"])
            if not path.is_file():
                continue
            if progress:
                progress(current, total, f"匹配: {path.name}")
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
        return [replace(result, thumbnail=make_thumbnail(result.path)) for result in scored[:top_n]]

    def count(self, root: Path) -> int:
        root = root.resolve()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select count(*) as count from images where root = ? and descriptors is not null",
                (str(root),),
            ).fetchone()
            return int(row["count"])

    def status(self, root: Path) -> IndexStatus:
        root = root.resolve()
        files = list(iter_image_files(root))
        current: dict[str, os.stat_result] = {}
        for path in files:
            resolved = path.resolve()
            current[str(resolved)] = resolved.stat()

        with closing(self._connect()) as conn:
            rows = {
                row["path"]: row
                for row in conn.execute(
                    """
                    select path, size, mtime, updated_at, error
                    from images
                    where root = ?
                    """,
                    (str(root),),
                )
            }

        current_paths = set(current)
        indexed_paths = set(rows)
        new_files = len(current_paths - indexed_paths)
        removed_files = len(indexed_paths - current_paths)
        changed_files = 0
        error_files = 0
        indexed_files = 0
        last_indexed_at: Optional[float] = None

        for path_text in current_paths & indexed_paths:
            stat = current[path_text]
            row = rows[path_text]
            if row["updated_at"] is not None:
                updated_at = float(row["updated_at"])
                last_indexed_at = updated_at if last_indexed_at is None else max(last_indexed_at, updated_at)
            if row["error"]:
                error_files += 1
            if row["size"] != stat.st_size or row["mtime"] != stat.st_mtime:
                changed_files += 1
                continue
            if not row["error"]:
                indexed_files += 1

        for path_text in indexed_paths - current_paths:
            row = rows[path_text]
            if row["updated_at"] is not None:
                updated_at = float(row["updated_at"])
                last_indexed_at = updated_at if last_indexed_at is None else max(last_indexed_at, updated_at)

        return IndexStatus(
            total_files=len(files),
            indexed_files=indexed_files,
            new_files=new_files,
            changed_files=changed_files,
            removed_files=removed_files,
            error_files=error_files,
            last_indexed_at=last_indexed_at,
        )

    def _feature_rows(self, root: Path) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return list(
                conn.execute(
                    """
                    select path, width, height, keypoints, descriptors
                    from images
                    where root = ? and descriptors is not null and keypoints is not null
                    """,
                    (str(root),),
                )
            )

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


def serialize_empty_keypoints() -> bytes:
    from io import BytesIO

    import numpy as np

    buffer = BytesIO()
    np.save(buffer, np.empty((0, 2), dtype=np.float32), allow_pickle=False)
    return buffer.getvalue()


def default_max_workers() -> int:
    return max(1, min(8, os.cpu_count() or 4))


def extract_index_results(
    jobs: list[IndexJob],
    *,
    max_workers: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Iterable[IndexResult]:
    if not jobs:
        return
    workers = max(1, max_workers or default_max_workers())
    if workers == 1 or len(jobs) == 1:
        for job in jobs:
            if cancel_event and cancel_event.is_set():
                return
            yield extract_index_job(job)
        return

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = [executor.submit(extract_index_job, job) for job in jobs]
    try:
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break
            yield future.result()
    finally:
        for future in futures:
            future.cancel()
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
