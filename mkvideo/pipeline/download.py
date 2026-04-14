"""Step 1: Download video from YouTube via yt-dlp."""

from pathlib import Path

import yt_dlp


def download_video(url: str, out_dir: Path) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
    }
    print(f"\n[1/5] Downloading: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info["id"]
        video_title = info.get("title", "")
        video_path = out_dir / f"{video_id}.mp4"
        if not video_path.exists():
            candidates = list(out_dir.glob(f"{video_id}.*"))
            if not candidates:
                raise FileNotFoundError(f"Downloaded file not found for id={video_id}")
            video_path = candidates[0]
    print(f"   Saved: {video_path}  ({video_title})")
    return video_path, video_title


def fetch_video_title(url: str) -> str:
    """Fetch YouTube video title without downloading (used with --skip-download)."""
    try:
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "")
    except Exception:
        return ""
