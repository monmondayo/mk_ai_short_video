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

    def _upsert(
        self,
        table: str,
        data: dict | list[dict],
        on_conflict: str,
    ) -> dict:
        """Insert or update via PostgREST merge-duplicates.

        Uses the ``Prefer: resolution=merge-duplicates`` header together
        with ``?on_conflict=...`` so a repeated run over the same
        (job_id, rank) etc. overwrites instead of 409-ing.
        """
        url = self._rest_url(table)
        headers = dict(self.headers)
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        params = {"on_conflict": on_conflict}
        resp = requests.post(url, headers=headers, params=params, json=data)
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
        upper_text_color: str = "#000000",
        upper_edge_color: str = "#FFDC00",
        subtitle_text_color: str = "#FFFFFF",
        subtitle_edge_color: str = "#FF1493",
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
            "upper_text_color": upper_text_color,
            "upper_edge_color": upper_edge_color,
            "subtitle_text_color": subtitle_text_color,
            "subtitle_edge_color": subtitle_edge_color,
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

    def get_stories(self, job_id: str) -> list[dict] | None:
        """Return the ``stories_json`` array saved for this job, or None."""
        url = self._rest_url("stories")
        params = {"job_id": f"eq.{job_id}", "select": "stories_json"}
        resp = requests.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        return rows[0].get("stories_json")

    # ── Subtitle operations ───────────────────────────────────────────
    # ``subtitles_json`` on the stories row mirrors the CLI's
    # --review-subtitles intermediate state: after Phase 1 cuts + joins
    # + generates SRTs we stash them here so the browser can edit them.

    def save_subtitles(self, job_id: str, subtitles_json: dict) -> None:
        """Upsert the subtitles_json blob for a job.

        ``subtitles_json`` is keyed by rank-as-string::

            {
              "1": {
                "srt": "1\\n00:00:00,000 --> ...\\n...",
                "durations": [10.5, 8.2],
                "joined_key": "joined/s1.mp4"
              },
              ...
            }

        We always update the existing stories row (extract_stories_job
        creates it), so no fallback insert is needed.
        """
        self._patch(
            "stories",
            {"job_id": job_id},
            {"subtitles_json": subtitles_json},
        )

    def get_subtitles(self, job_id: str) -> dict | None:
        """Return the ``subtitles_json`` blob for this job, or None."""
        url = self._rest_url("stories")
        params = {"job_id": f"eq.{job_id}", "select": "subtitles_json"}
        resp = requests.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        return rows[0].get("subtitles_json")

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
        """Record a finished output video (upsert on job_id+rank).

        Using upsert lets a re-run of the render phase overwrite the
        previous row for the same (job_id, rank) instead of failing with
        a 409 unique-constraint conflict.
        """
        self._upsert(
            "output_videos",
            {
                "job_id": job_id,
                "rank": rank,
                "title": title,
                "r2_url": r2_url,
                "duration_sec": duration_sec,
                "file_size_mb": file_size_mb,
            },
            on_conflict="job_id,rank",
        )

    # ── Job retrieval ─────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict | None:
        """Get job details."""
        url = self._rest_url("jobs")
        params = {"id": f"eq.{job_id}", "select": "*"}
        resp = requests.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None
