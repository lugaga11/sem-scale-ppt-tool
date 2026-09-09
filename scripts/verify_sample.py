"""Check screenshot matching against an optional local gallery, using a temporary index."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from sem_tool.search.index import ImageIndex


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", type=Path)
    parser.add_argument("gallery", type=Path)
    parser.add_argument("expected", help="Expected top result, relative to the gallery")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sem_tool_verify_") as temp:
        index = ImageIndex(Path(temp) / "index.sqlite")
        stats = index.update(args.gallery)
        results = index.search(args.gallery, args.query, top_n=5)
        print(f"indexed={stats.indexed}, skipped={stats.skipped}, errors={stats.errors}")
        for rank, result in enumerate(results, start=1):
            print(f"{rank}: {result.path.relative_to(args.gallery.resolve())}, inliers={result.inliers}")
        return 0 if results and results[0].path == (args.gallery / args.expected).resolve() else 1


if __name__ == "__main__":
    raise SystemExit(main())
