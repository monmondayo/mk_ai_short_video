"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """Interface for file storage operations.
    LocalStorage uses the filesystem directly.
    R2Storage (future) will use Cloudflare R2 via S3-compatible API.
    """

    @abstractmethod
    def save_json(self, data: dict, key: str) -> str:
        """Save JSON data. Returns a reference (path or URL)."""
        ...

    @abstractmethod
    def load_json(self, key: str) -> dict | None:
        """Load JSON data by key. Returns None if not found."""
        ...

    @abstractmethod
    def save_file(self, local_path: Path, key: str) -> str:
        """Upload a local file. Returns a reference (path or URL)."""
        ...

    @abstractmethod
    def get_file(self, key: str, local_path: Path) -> Path:
        """Download a file to local_path. Returns the local path."""
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists in storage."""
        ...
