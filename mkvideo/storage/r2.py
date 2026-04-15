"""Cloudflare R2 storage backend (S3-compatible) for cloud deployment."""

import json
import os
from pathlib import Path

import boto3
from botocore.config import Config

from .base import StorageBackend


class R2Storage(StorageBackend):
    """Store files on Cloudflare R2 via S3-compatible API.

    Required environment variables:
        R2_ACCOUNT_ID       - Cloudflare account ID
        R2_ACCESS_KEY_ID    - R2 API token access key
        R2_SECRET_ACCESS_KEY - R2 API token secret key
        R2_BUCKET_NAME      - R2 bucket name (default: mkvideo)
    """

    def __init__(self, prefix: str = ""):
        account_id = os.environ["R2_ACCOUNT_ID"]
        self.bucket_name = os.environ.get("R2_BUCKET_NAME", "mkvideo")
        self.prefix = prefix.strip("/")

        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(
                region_name="auto",
                s3={"addressing_style": "path"},
            ),
        )

    def _key(self, key: str) -> str:
        """Build full S3 key with optional prefix."""
        if self.prefix:
            return f"{self.prefix}/{key}"
        return key

    def save_json(self, data: dict, key: str) -> str:
        """Save JSON data to R2. Returns the S3 key."""
        full_key = self._key(key)
        body = json.dumps(data, ensure_ascii=False, indent=2)
        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=full_key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        return full_key

    def load_json(self, key: str) -> dict | None:
        """Load JSON data from R2. Returns None if not found."""
        full_key = self._key(key)
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=full_key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception:
            return None

    def save_file(self, local_path: Path, key: str) -> str:
        """Upload a local file to R2. Returns the S3 key."""
        full_key = self._key(key)
        content_type = self._guess_content_type(local_path)
        self.s3.upload_file(
            str(local_path),
            self.bucket_name,
            full_key,
            ExtraArgs={"ContentType": content_type},
        )
        return full_key

    def get_file(self, key: str, local_path: Path) -> Path:
        """Download a file from R2 to local_path."""
        full_key = self._key(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.bucket_name, full_key, str(local_path))
        return local_path

    def exists(self, key: str) -> bool:
        """Check if a key exists in R2."""
        full_key = self._key(key)
        try:
            self.s3.head_object(Bucket=self.bucket_name, Key=full_key)
            return True
        except Exception:
            return False

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for downloading a file.

        Args:
            key: S3 key (without prefix, will be added automatically)
            expires_in: URL expiry in seconds (default: 1 hour)
        """
        full_key = self._key(key)
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": full_key},
            ExpiresIn=expires_in,
        )

    def generate_upload_url(self, key: str, expires_in: int = 3600, content_type: str = "application/octet-stream") -> str:
        """Generate a presigned URL for uploading a file (used by frontend).

        Args:
            key: S3 key (without prefix, will be added automatically)
            expires_in: URL expiry in seconds (default: 1 hour)
            content_type: MIME type of the file to upload
        """
        full_key = self._key(key)
        return self.s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": full_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    @staticmethod
    def _guess_content_type(path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".mp4": "video/mp4",
            ".json": "application/json",
            ".srt": "text/plain",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".txt": "text/plain",
        }.get(suffix, "application/octet-stream")
