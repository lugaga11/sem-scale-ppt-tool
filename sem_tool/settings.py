"""User-local storage, retaining the original search index and preferences."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def data_directory() -> Path:
    override = os.environ.get("SEM_TOOL_DATA_DIR")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    local = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return local / "ImageOriginFinder"


def default_db_path() -> Path:
    return data_directory() / "index.sqlite"


@dataclass
class AppSettings:
    last_gallery_dir: str = ""

    @classmethod
    def load(cls) -> AppSettings:
        try:
            data = json.loads((data_directory() / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        gallery = data.get("last_gallery_dir", "") if isinstance(data, dict) else ""
        return cls(last_gallery_dir=gallery if isinstance(gallery, str) else "")

    def save(self) -> None:
        directory = data_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "settings.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
