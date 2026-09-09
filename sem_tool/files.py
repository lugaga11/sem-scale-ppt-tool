"""Shared image discovery and native path handling."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

IMAGE_EXTENSIONS = frozenset({".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"})
IMAGE_FILE_FILTER = "图片 (" + " ".join(f"*{suffix}" for suffix in sorted(IMAGE_EXTENSIONS)) + ")"


def path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def natural_key(path: Path) -> list[str | int]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", str(path).casefold())]


def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def iter_image_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        raise ValueError(f"图库目录不存在: {root}")
    for path in root.rglob("*"):
        if is_supported_image(path) and path.is_file():
            yield path


def image_paths_from_inputs(paths: Iterable[Path]) -> list[Path]:
    """Preserve explicit queue order; sort folder contents and deduplicate paths."""
    images: dict[str, Path] = {}
    for path in paths:
        if path.is_dir():
            candidates = sorted(iter_image_files(path), key=natural_key)
        elif path.is_file() and is_supported_image(path):
            candidates = [path]
        else:
            continue
        for candidate in candidates:
            images.setdefault(path_key(candidate), candidate.resolve())
    return list(images.values())


def find_images(input_paths: Path | Iterable[Path]) -> list[Path]:
    images = image_paths_from_inputs([input_paths] if isinstance(input_paths, Path) else input_paths)
    if not images:
        raise ValueError("未找到支持的图片文件。")
    return images


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def reveal_path(path: Path) -> None:
    path = path.resolve()
    if sys.platform == "win32" and path.is_file():
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    else:
        open_path(path if path.is_dir() else path.parent)
