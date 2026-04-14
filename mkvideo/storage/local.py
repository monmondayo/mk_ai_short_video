"""Local filesystem storage backend for CLI usage."""

import json
import shutil
from pathlib import Path

from .base import StorageBackend


class LocalStorage(StorageBackend):
    """Store files on the local filesystem (default for CLI mode)."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        return self.base_dir / key

    def save_json(self, data: dict, key: str) -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return str(path)

    def load_json(self, key: str) -> dict | None:
        path = self._resolve(key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def save_file(self, local_path: Path, key: str) -> str:
        dst = self._resolve(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if local_path.resolve() != dst.resolve():
            shutil.copy2(local_path, dst)
        return str(dst)

    def get_file(self, key: str, local_path: Path) -> Path:
        src = self._resolve(key)
        if not src.exists():
            raise FileNotFoundError(f"File not found in storage: {key}")
        if src.resolve() != local_path.resolve():
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, local_path)
        return local_path

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()
