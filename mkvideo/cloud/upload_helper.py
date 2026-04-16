"""
Upload helper: download video locally (yt-dlp) and upload to R2.

This module handles the "local download → R2 upload" flow that is needed
because Modal's datacenter IPs are blocked by YouTube.

Usage (CLI):
    from mkvideo.cloud.upload_helper import download_and_upload_to_r2
    r2_key = download_and_upload_to_r2(job_id, youtube_url, tmp_dir)

Usage (standalone):
    python -m mkvideo.cloud.upload_helper <job_id> <youtube_url>
"""

import sys
from pathlib import Path

from ..pipeline.download import download_video, fetch_video_title
from ..storage.r2 import R2Storage


def download_and_upload_to_r2(
    job_id: str,
    youtube_url: str,
    tmp_dir: Path | None = None,
) -> dict:
    """Download video with yt-dlp locally and upload to R2.

    Args:
        job_id: Unique job identifier (used as R2 prefix: jobs/{job_id}/source/)
        youtube_url: YouTube URL to download
        tmp_dir: Temporary directory for download (auto-created if None)

    Returns:
        dict with keys: r2_key, video_title, local_path
    """
    import tempfile

    cleanup = False
    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="mkvideo_upload_"))
        cleanup = True

    try:
        # Download locally (yt-dlp works from user's IP, not datacenter)
        print(f"[Upload] Downloading video from YouTube...")
        video_path, video_title = download_video(youtube_url, tmp_dir)
        print(f"[Upload] Downloaded: {video_path.name} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)")

        # Upload to R2
        print(f"[Upload] Uploading to R2 (jobs/{job_id}/source/{video_path.name})...")
        r2 = R2Storage(prefix=f"jobs/{job_id}")
        r2_key = f"source/{video_path.name}"
        r2.save_file(video_path, r2_key)
        full_r2_key = r2._key(r2_key)
        print(f"[Upload] Upload complete: {full_r2_key}")

        return {
            "r2_key": full_r2_key,
            "video_title": video_title,
            "local_path": video_path,
        }
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


def upload_local_video_to_r2(
    job_id: str,
    video_path: Path,
) -> dict:
    """Upload an already-downloaded local video to R2.

    Args:
        job_id: Unique job identifier
        video_path: Path to local video file

    Returns:
        dict with keys: r2_key
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    print(f"[Upload] Uploading {video_path.name} to R2 ({video_path.stat().st_size / 1024 / 1024:.1f} MB)...")
    r2 = R2Storage(prefix=f"jobs/{job_id}")
    r2_key = f"source/{video_path.name}"
    r2.save_file(video_path, r2_key)
    full_r2_key = r2._key(r2_key)
    print(f"[Upload] Upload complete: {full_r2_key}")

    return {"r2_key": full_r2_key}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m mkvideo.cloud.upload_helper <job_id> <youtube_url>")
        sys.exit(1)

    job_id = sys.argv[1]
    youtube_url = sys.argv[2]
    result = download_and_upload_to_r2(job_id, youtube_url)
    print(f"\nDone! Video uploaded to R2:")
    print(f"  R2 key: {result['r2_key']}")
    print(f"  Title: {result['video_title']}")
