"""Supabase client for job status tracking and progress updates.

Used by Modal workers to update job status in the Supabase database.
The frontend subscribes to Supabase Realtime to receive live updates.

Required environment variables:
    SUPABASE_URL       - Supabase project URL (e.g. https://xxx.supabase.co)
    SUPABASE_SERVICE_KEY - Supabase service role key (for server-side access)
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests


class SupabaseJobClient:
    """Lightweight REST client for updating job status in Supabase.

    Uses raw HTTP requests instead of the supabase-py SDK to minimize
    dependencies in the Modal container image.
    """

    def __init__(self):
        self.url = os.environ["SUPABASE_URL"]
        self.key = os.environ["SUPABASE_SERVICE_KEY"]
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _rest_url(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    def _patch(self, table: str, match: dict, data: dict) -> dict:
        """Update rows matching the given filters."""
        url = self._rest_url(table)
        params = {f"{k}": f"eq.{v}" for k, v in match.items()}
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        resp = requests.patch(url, headers=self.headers, params=params, json=data)
        resp.raise_for_status()
        return resp.json()

    def _post(self, table: str, data: dict) -> dict:
        """Insert a new row."""
        url = self._rest_url(table)
        resp = requests.post(url, headers=self.headers, json=data)
        resp.raise_for_status()
        return resp.json()

    def _get(self, table: str, match: dict, select: str = "*") -> list[dict]:
        """Select rows matching the given filters."""
        url = self._rest_url(table)
        params = {f"{k}": f"eq.{v}" for k, v in match.items()}
        params["select"] = select
        resp = requests.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Job creation ─────────────────────────────────────────────────

    def create_job(
        self,
        user_id: str,
        youtube_url: str,
        video_title: str = "",
        whisper_model: str = "medium",
        whisper_language: str = "ja",
        num_stories: int = 10,
        duration_preset: str = "30-60",
        bg_color: str = "white",
        add_captions: bool = True,
        skip_proofread: bool = False,
    ) -> dict:
        """Create a new job and return the row (including generated UUID)."""
        data = {
            "user_id": user_id,
            "youtube_url": youtube_url,
            "video_title": video_title,
            "whisper_model": whisper_model,
            "whisper_language": whisper_language,
            "num_stories": num_stories,
            "duration_preset": duration_preset,
            "bg_color": bg_color,
            "add_captions": add_captions,
            "skip_proofread": skip_proofread,
        }
        rows = self._post("jobs", data)
        return rows[0] if isinstance(rows, list) and rows else rows

    # ── Job status updates ────────────────────────────────────────────

    def update_job_status(
        self,
        job_id: str,
        status: str,
        progress: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update job status and optional progress JSON.

        Status values: pending, downloading, transcribing, proofreading,
                       awaiting_review, extracting, rendering, complete, failed
        """
        data: dict[str, Any] = {"status": status}
        if progress is not None:
            data["progress"] = json.dumps(progress) if isinstance(progress, dict) else progress
        if error_message is not None:
            data["error_message"] = error_message
        self._patch("jobs", {"id": job_id}, data)

    def update_job_progress(
        self,
        job_id: str,
        step: str,
        current: int,
        total: int,
        detail: str = "",
    ) -> None:
        """Update the progress JSONB field on a job (triggers Supabase Realtime)."""
        progress = {"step": step, "current": current, "total": total, "detail": detail}
        self._patch("jobs", {"id": job_id}, {"progress": progress})

    # ── Transcript operations ─────────────────────────────────────────

    def save_transcript(
        self,
        job_id: str,
        segments: list[dict],
        raw_json_url: str = "",
        proofread_json_url: str = "",
    ) -> None:
        """Upsert transcript data for a job."""
        data = {
            "job_id": job_id,
            "segments": segments,
            "raw_json_url": raw_json_url,
            "proofread_json_url": proofread_json_url,
        }
        # Try update first, then insert
        result = self._patch("transcripts", {"job_id": job_id}, data)
        if not result:
            self._post("transcripts", data)

    def get_transcript(self, job_id: str) -> dict | None:
        """Get transcript for a job."""
        url = self._rest_url("transcripts")
        params = {"job_id": f"eq.{job_id}", "select": "*"}
        resp = requests.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    # ── Story operations ──────────────────────────────────────────────

    def save_stories(self, job_id: str, stories_json: list[dict]) -> None:
        """Save extracted stories for a job."""
        data = {"job_id": job_id, "stories_json": stories_json}
        result = self._patch("stories", {"job_id": job_id}, data)
        if not result:
            self._post("stories", data)

    # ── Output video operations ───────────────────────────────────────

    def save_output_video(
        self,
        job_id: str,
        rank: int,
        title: str,
        r2_url: str,
        duration_sec: float,
        file_size_mb: float,
    ) -> None:
        """Record a finished output video."""
        self._post("output_videos", {
            "job_id": job_id,
            "rank": rank,
            "title": title,
            "r2_url": r2_url,
            "duration_sec": duration_sec,
            "file_size_mb": file_size_mb,
        })

    # ── Job retrieval ─────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict | None:
        """Get job details."""
        url = self._rest_url("jobs")
        params = {"id": f"eq.{job_id}", "select": "*"}
        resp = requests.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None
