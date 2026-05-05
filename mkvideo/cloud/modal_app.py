"""
Modal serverless deployment for mkvideo.

Two main functions:
  1. transcribe_video_job - Run Whisper on video from R2, proofread with Claude
  2. extract_and_render - Extract stories with Claude, render all short videos

Web API endpoints:
  POST /submit-job       - Start a new job (returns job_id + call_id)
  POST /start-render     - Start phase 2 after transcript review
  GET  /job-status/{id}  - Poll job status

Deploy:
  modal deploy mkvideo/cloud/modal_app.py

Prerequisites:
  modal secret create mkvideo-secrets \
    ANTHROPIC_API_KEY=sk-ant-... \
    SUPABASE_URL=https://xxx.supabase.co \
    SUPABASE_SERVICE_KEY=eyJ... \
    R2_ACCOUNT_ID=... \
    R2_ACCESS_KEY_ID=... \
    R2_SECRET_ACCESS_KEY=... \
    R2_BUCKET_NAME=mkvideo

Note:
  Video downloading (yt-dlp) is NOT done on Modal because datacenter IPs
  are blocked by YouTube. Videos must be downloaded locally and uploaded
  to R2 before submitting a job. Use cloud/upload_helper.py for this.
"""

import re

import modal

# ── Container image ──────────────────────────────────────────────────
# Installs ffmpeg, CJK fonts, and all Python dependencies.
# Note: yt-dlp is NOT included — video download happens outside Modal.
# Whisper model weights are cached in a persistent Volume.
mkvideo_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "fonts-noto-cjk")
    .pip_install(
        "openai-whisper>=20231117",
        "anthropic>=0.20.0",
        "python-dotenv>=1.0.0",
        "Pillow>=10.0.0",
        "Janome>=0.5.0",
        "boto3>=1.34.0",
        "requests>=2.31.0",
        # Required by the @modal.asgi_app() web API (FastAPI + ASGI server)
        "fastapi>=0.110.0",
        "pydantic>=2.0.0",
    )
    # Copy local mkvideo package into the container image
    # so that `from mkvideo.pipeline.transcribe import ...` works.
    .add_local_dir("mkvideo", remote_path="/root/mkvideo")
)

app = modal.App("mkvideo", image=mkvideo_image)

# Persistent volume for Whisper model weights (avoids re-download on cold start)
whisper_cache = modal.Volume.from_name("mkvideo-whisper-cache", create_if_missing=True)

# Shared secret for all API keys
secrets = modal.Secret.from_name("mkvideo-secrets")

# ── Constants ────────────────────────────────────────────────────────
WHISPER_CACHE_PATH = "/cache/whisper"


# ── Helper: download video from R2 ──────────────────────────────────
def _download_video_from_r2(r2, job_id: str, dest_dir) -> "Path":
    """Find and download the source video from R2 for a given job."""
    from pathlib import Path

    dest_dir = Path(dest_dir)
    prefix_key = r2._key("source/")
    response = r2.s3.list_objects_v2(
        Bucket=r2.bucket_name, Prefix=prefix_key, MaxKeys=10
    )
    video_key = None
    video_ext = ".mp4"
    for obj in response.get("Contents", []):
        key = obj["Key"]
        for ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
            if key.lower().endswith(ext):
                video_key = key
                video_ext = ext
                break
        if video_key:
            break
    if not video_key:
        raise RuntimeError(f"Source video not found in R2 at {prefix_key}")

    # IMPORTANT: use a short fixed local filename — the R2 key can contain
    # a long Japanese title, and boto3's transfer manager appends a random
    # suffix (".<hex>") to the destination path while writing. A long title
    # + suffix easily exceeds the 255-byte per-component filesystem limit
    # on Linux (ext4), causing OSError: [Errno 36] File name too long.
    # The filename isn't meaningful downstream: ffmpeg/Whisper only need
    # the extension to dispatch, and the user-facing title is stored
    # separately in the jobs table.
    video_path = dest_dir / f"source{video_ext}"
    r2.s3.download_file(r2.bucket_name, video_key, str(video_path))
    return video_path


# ── Job 1: Transcribe (video already in R2) ─────────────────────────
@app.function(
    secrets=[secrets],
    volumes={WHISPER_CACHE_PATH: whisper_cache},
    gpu="T4",       # GPU for Whisper; use gpu=None for base/tiny models on CPU
    timeout=3600,   # 1 hour max
    memory=16384,   # 16 GB RAM
)
def transcribe_video_job(
    job_id: str,
    whisper_model: str = "medium",
    whisper_language: str = "ja",
    skip_proofread: bool = False,
) -> dict:
    """Phase 1: Download video FROM R2, transcribe with Whisper, proofread with Claude.

    The video must already be uploaded to R2 at jobs/{job_id}/source/<filename>.mp4
    (use cloud/upload_helper.py or the frontend upload to put it there).

    Updates job status in Supabase at each step.
    Returns dict with transcript data and R2 keys.
    """
    import os
    import tempfile

    # Set Whisper cache dir to persistent volume
    os.environ["XDG_CACHE_HOME"] = WHISPER_CACHE_PATH

    from mkvideo.cloud.supabase_client import SupabaseJobClient
    from mkvideo.pipeline.transcribe import proofread_transcript, transcribe_video
    from mkvideo.storage.r2 import R2Storage

    db = SupabaseJobClient()
    r2 = R2Storage(prefix=f"jobs/{job_id}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: Download video from R2
            db.update_job_status(job_id, "downloading")
            db.update_job_progress(job_id, "downloading", 0, 1, "Downloading video from storage...")
            video_path = _download_video_from_r2(r2, job_id, tmpdir)
            db.update_job_progress(job_id, "downloading", 1, 1, "Download complete")

            # Step 2: Transcribe (with live progress forwarded to Supabase)
            db.update_job_status(job_id, "transcribing")
            db.update_job_progress(
                job_id, "transcribing", 0, 100,
                "Loading Whisper model...",
            )
            language = whisper_language if whisper_language else None

            import time
            _state = {"last_t": 0.0, "last_pct": -1}

            def _progress(frac: float) -> None:
                """Throttle: update at most ~once/sec AND only on %-changes.

                Avoids overwhelming Supabase Realtime with hundreds of
                writes during a long transcription.
                """
                now = time.monotonic()
                pct = int(frac * 100)
                if pct == _state["last_pct"]:
                    return
                if pct < 100 and (now - _state["last_t"]) < 1.0:
                    return
                _state["last_pct"] = pct
                _state["last_t"] = now
                try:
                    db.update_job_progress(
                        job_id, "transcribing",
                        current=pct, total=100,
                        detail=f"Transcribing audio ({pct}%)",
                    )
                except Exception as e:
                    print(f"[transcribe progress update failed] {e}")

            transcript = transcribe_video(
                video_path,
                model_name=whisper_model,
                language=language,
                progress_callback=_progress,
            )

            # Save raw transcript to R2
            raw_key = "transcript/raw.json"
            r2.save_json(transcript, raw_key)

            # Commit whisper cache volume so model weights persist
            whisper_cache.commit()

            # Step 3: Proofread (optional)
            proofread_key = ""
            if not skip_proofread:
                db.update_job_status(job_id, "proofreading")
                transcript = proofread_transcript(transcript)
                proofread_key = "transcript/proofread.json"
                r2.save_json(transcript, proofread_key)

            # Save transcript to Supabase for the web editor
            db.save_transcript(
                job_id,
                segments=transcript["segments"],
                raw_json_url=raw_key,
                proofread_json_url=proofread_key,
            )

            # Update status to awaiting_review
            db.update_job_status(
                job_id, "awaiting_review",
                progress={"step": "awaiting_review", "current": 0, "total": 0,
                          "detail": "Transcript ready for review"},
            )

            return {
                "job_id": job_id,
                "segment_count": len(transcript["segments"]),
            }

    except Exception as e:
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise


# ── Job 2a: Extract stories (Claude only, fast) ─────────────────────
# Splitting extract from render lets the user edit story titles/hooks
# in the web editor before we spend minutes rendering.
@app.function(
    secrets=[secrets],
    timeout=900,    # 15 min is plenty for Claude extraction
    memory=4096,
)
def extract_stories_job(
    job_id: str,
    num_stories: int = 10,
    duration_preset: str = "30-60",
) -> dict:
    """Phase 2a: Extract stories with Claude, save to Supabase.

    On success sets status to ``awaiting_story_review`` so the user can
    edit titles/hooks before rendering. Render is kicked off separately
    via :func:`render_videos_job`.
    """
    import tempfile
    from pathlib import Path

    from mkvideo.cloud.supabase_client import SupabaseJobClient
    from mkvideo.pipeline.constants import (
        DURATION_PRESETS,
        DURATION_TOLERANCE_SEC,
    )
    from mkvideo.pipeline.render import get_video_info
    from mkvideo.pipeline.stories import enforce_story_duration_range, extract_stories
    from mkvideo.storage.r2 import R2Storage

    db = SupabaseJobClient()
    r2 = R2Storage(prefix=f"jobs/{job_id}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            db.update_job_status(job_id, "extracting")

            # We need the video only to read its duration (ffprobe).
            # Could be optimized later by storing duration at upload time.
            video_path = _download_video_from_r2(r2, job_id, tmp)

            transcript_data = db.get_transcript(job_id)
            if not transcript_data:
                raise RuntimeError("Transcript not found")
            transcript = {
                "text": "".join(s["text"] for s in transcript_data["segments"]),
                "segments": transcript_data["segments"],
            }

            info = get_video_info(video_path)

            min_dur, max_dur = DURATION_PRESETS.get(duration_preset, (30, 60))

            def _story_progress(current: int, total: int, detail: str) -> None:
                try:
                    db.update_job_progress(
                        job_id, "extracting",
                        current=current, total=total,
                        detail=detail,
                    )
                except Exception as e:
                    print(f"[extracting progress update failed] {e}")

            stories = extract_stories(
                transcript,
                num_stories=num_stories,
                video_duration=info["duration"],
                min_dur=min_dur,
                max_dur=max_dur,
                progress_callback=_story_progress,
            )

            enforce_story_duration_range(
                stories,
                min_total=min_dur,
                max_total=max_dur,
                video_duration=info["duration"],
                tolerance_sec=DURATION_TOLERANCE_SEC,
            )

            db.save_stories(job_id, stories)

            db.update_job_status(
                job_id, "awaiting_story_review",
                progress={
                    "step": "awaiting_story_review",
                    "current": len(stories),
                    "total": len(stories),
                    "detail": "Stories ready for review",
                },
            )
            return {"job_id": job_id, "story_count": len(stories)}

    except Exception as e:
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise


# ── Helpers shared between prepare/render ────────────────────────────
def _strip_video_extension(title: str) -> str:
    """Strip trailing ``.mp4`` / ``.mkv`` / etc. from ``title``.

    Shared between the render-only and prepare-subtitles paths so the
    top-of-frame text never shows the extension. Strips surrounding
    whitespace first so trailing spaces don't hide the extension from
    the regex anchor.
    """
    t = (title or "").strip()
    t = re.sub(
        r"\.(mp4|mkv|mov|webm|avi|m4v|flv|wmv|mpg|mpeg|ts)$",
        "", t, flags=re.IGNORECASE,
    ).strip()
    return t


def _safe_output_stem(story: dict) -> str:
    """Sanitize story title for use as the output filename stem.

    Mirrors the logic inside ``render.render_story`` so the filenames
    produced by the two-phase flow match the one-shot flow.
    """
    title = story.get("title", "")
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    return safe[:50].strip() or f"story_{story.get('rank', 0)}"


def _download_input_images(r2, constants_module) -> list:
    """Download overlay images from ``jobs/<id>/input/`` into INPUT_DIR.

    Returns a list of local ``Path`` s. Failures are logged and swallowed
    — rendering can still proceed without user-supplied overlay images.
    """
    from pathlib import Path

    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    input_prefix = r2._key("input/")
    images = []
    try:
        paginator = r2.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=r2.bucket_name, Prefix=input_prefix
        ):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key.rsplit("/", 1)[-1]
                if not name:
                    continue
                if Path(name).suffix.lower() not in image_exts:
                    continue
                dest = constants_module.INPUT_DIR / name
                r2.s3.download_file(r2.bucket_name, key, str(dest))
                images.append(dest)
    except Exception as e:
        print(f"[_download_input_images] No input images loaded: {e}")
    return images


# ── Job 2b-prep: Cut + join + SRT, then pause for user review ────────
# This is the cloud equivalent of the CLI's --review-subtitles pause.
# Only called when the user explicitly opts into subtitle editing via
# the StoryEditor checkbox. Normal render (no checkbox) skips this and
# calls render_videos_job directly against the raw source video.
@app.function(
    secrets=[secrets],
    timeout=7200,   # 2 hours max
    memory=8192,
    cpu=4.0,
)
def prepare_subtitles_job(
    job_id: str,
    duration_preset: str = "30-60",
    bg_color: str = "white",
) -> dict:
    """Phase 2b-prep: cut segments + join + generate SRT per story.

    Uploads each joined intermediate video to R2 at
    ``jobs/<id>/joined/s<rank>.mp4`` and stores the generated SRT plus
    per-segment durations in ``stories.subtitles_json``. Flips the job
    to ``awaiting_subtitle_review`` when done.

    The follow-up :func:`render_videos_job` detects ``subtitles_json``
    and skips back to Phase 2 (overlay only) using the (possibly edited)
    SRT text from the DB.
    """
    import tempfile
    from pathlib import Path

    from mkvideo.cloud.supabase_client import SupabaseJobClient
    from mkvideo.pipeline.constants import DURATION_PRESETS, DURATION_TOLERANCE_SEC
    from mkvideo.pipeline.render import _prepare_story
    from mkvideo.storage.r2 import R2Storage

    db = SupabaseJobClient()
    r2 = R2Storage(prefix=f"jobs/{job_id}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            job = db.get_job(job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} not found")

            stories = db.get_stories(job_id)
            if not stories:
                raise RuntimeError("Stories not found — extraction must run first")

            transcript_data = db.get_transcript(job_id)
            if not transcript_data:
                raise RuntimeError("Transcript not found")
            transcript_segs = transcript_data["segments"]

            db.update_job_status(job_id, "preparing_subtitles")
            db.update_job_progress(
                job_id, "preparing_subtitles",
                current=0, total=len(stories),
                detail="Downloading source video...",
            )
            video_path = _download_video_from_r2(r2, job_id, tmp)

            # _prepare_story uses constants.TEMP_DIR for SRT dumps.
            import mkvideo.pipeline.constants as constants
            constants.TEMP_DIR = tmp / "temp"
            constants.TEMP_DIR.mkdir()
            work_dir = tmp / "work"
            work_dir.mkdir()

            _, max_dur = DURATION_PRESETS.get(duration_preset, (30, 60))
            max_total = max_dur + DURATION_TOLERANCE_SEC

            subtitles: dict[str, dict] = {}
            # Collect per-story failures so we can surface them to the user
            # rather than hiding them behind a generic "no stories prepared"
            # error. Each entry is "#<rank>: <ExceptionType>: <message>".
            errors: list[str] = []
            for i, story in enumerate(stories):
                rank = story["rank"]
                db.update_job_progress(
                    job_id, "preparing_subtitles",
                    current=i, total=len(stories),
                    detail=f"Preparing subtitles for story #{rank}",
                )
                try:
                    joined_path, durations, srt_content = _prepare_story(
                        story, video_path, transcript_segs, work_dir,
                        bg_color, True, max_total,
                    )
                except Exception as e:
                    # Skip stories that fail to cut/join rather than aborting
                    # the whole batch — the user can still review & render the
                    # stories that succeeded. Capture full traceback for Modal
                    # logs and a short summary for the UI/DB.
                    import traceback
                    tb = traceback.format_exc()
                    print(
                        f"[prepare_subtitles_job] story #{rank} failed:\n{tb}",
                        flush=True,
                    )
                    errors.append(f"#{rank}: {type(e).__name__}: {e}")
                    continue

                joined_key = f"joined/s{rank}.mp4"
                r2.save_file(joined_path, joined_key)

                subtitles[str(rank)] = {
                    "srt": srt_content,
                    "durations": list(durations),
                    "joined_key": joined_key,
                }

            if not subtitles:
                # Surface the real errors — the whole reason we collect them.
                # Cap length because Supabase error_message is a text column
                # and we don't want to dump a novel into the UI.
                summary = "; ".join(errors) if errors else "(no errors captured)"
                if len(summary) > 800:
                    summary = summary[:800] + "…"
                raise RuntimeError(
                    f"No stories could be prepared for subtitle review. "
                    f"Failures: {summary}"
                )

            db.save_subtitles(job_id, subtitles)
            db.update_job_status(
                job_id, "awaiting_subtitle_review",
                progress={
                    "step": "awaiting_subtitle_review",
                    "current": len(subtitles),
                    "total": len(stories),
                    "detail": "Subtitles ready for review",
                },
            )
            return {"job_id": job_id, "prepared": len(subtitles)}

    except Exception as e:
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise


# ── Job 2b: Render videos (heavy ffmpeg work) ────────────────────────
@app.function(
    secrets=[secrets],
    timeout=7200,   # 2 hours max (rendering is slow)
    memory=8192,    # 8 GB RAM
    cpu=4.0,        # 4 vCPUs for ffmpeg
)
def render_videos_job(
    job_id: str,
    duration_preset: str = "30-60",
    bg_color: str = "white",
    add_captions: bool = True,
) -> dict:
    """Phase 2b: Render all short videos from stories saved in Supabase.

    Two modes, selected by the presence of ``stories.subtitles_json``:

    - **Phase-2-only mode** (subtitles_json present): the user went
      through the subtitle review step, so we download the already-joined
      intermediate video from R2 and overlay the (possibly edited) SRT
      + title + images. Source video is NOT re-downloaded.
    - **Full mode** (no subtitles_json): legacy / default single-shot
      flow — download source video, run ``render_all_stories`` which
      cuts, joins, generates SRT, and overlays in one pass.

    Always reads the latest ``stories_json`` so StoryEditor edits apply.
    """
    import tempfile
    from pathlib import Path

    from mkvideo.cloud.supabase_client import SupabaseJobClient
    from mkvideo.pipeline.constants import DURATION_PRESETS
    from mkvideo.pipeline.render import (
        add_overlays,
        get_clip_duration,
        parse_hex_color,
        render_all_stories,
    )
    from mkvideo.storage.r2 import R2Storage

    db = SupabaseJobClient()
    r2 = R2Storage(prefix=f"jobs/{job_id}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / "output"
            out_dir.mkdir()

            job = db.get_job(job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} not found")

            stories = db.get_stories(job_id)
            if not stories:
                raise RuntimeError("Stories not found — extraction must run first")

            transcript_data = db.get_transcript(job_id)
            if not transcript_data:
                raise RuntimeError("Transcript not found")
            transcript = {
                "text": "".join(s["text"] for s in transcript_data["segments"]),
                "segments": transcript_data["segments"],
            }

            subtitles = db.get_subtitles(job_id)

            # Override TEMP_DIR / INPUT_DIR for ephemeral container work.
            # Both modes need these: render_all_stories writes SRT to
            # TEMP_DIR, add_overlays reads overlay images from INPUT_DIR.
            import mkvideo.pipeline.constants as constants
            constants.TEMP_DIR = tmp / "temp"
            constants.TEMP_DIR.mkdir()
            constants.INPUT_DIR = tmp / "input"
            constants.INPUT_DIR.mkdir()

            input_images = _download_input_images(r2, constants)
            if input_images:
                print(f"[render_videos_job] Loaded {len(input_images)} overlay images")

            _, max_dur = DURATION_PRESETS.get(duration_preset, (30, 60))
            video_title = _strip_video_extension(
                job.get("video_title", job.get("youtube_url", ""))
            )
            upper_text_color = parse_hex_color(job.get("upper_text_color"), (0, 0, 0))
            upper_edge_color = parse_hex_color(job.get("upper_edge_color"), (255, 220, 0))
            subtitle_text_color = parse_hex_color(job.get("subtitle_text_color"), (255, 255, 255))
            subtitle_edge_color = parse_hex_color(job.get("subtitle_edge_color"), (255, 20, 147))

            db.update_job_status(job_id, "rendering")

            output_paths: list[Path] = []

            if subtitles:
                # ── Phase-2-only mode ────────────────────────────────
                # Joined videos are already in R2 from prepare_subtitles_job;
                # SRTs are in subtitles[<rank>]["srt"] and may have been
                # edited by the user in the web UI.
                work_dir = tmp / "work"
                work_dir.mkdir()

                for i, story in enumerate(stories):
                    rank = story["rank"]
                    sub = subtitles.get(str(rank))
                    if not sub:
                        # Story was skipped during prepare (e.g. ffmpeg
                        # failed on Phase 1). Nothing to render.
                        continue

                    db.update_job_progress(
                        job_id, "rendering",
                        current=i, total=len(stories),
                        detail=f"Rendering story #{rank} (overlay)",
                    )

                    joined_local = work_dir / f"s{rank}_joined.mp4"
                    joined_full_key = r2._key(sub["joined_key"])
                    r2.s3.download_file(
                        r2.bucket_name, joined_full_key, str(joined_local)
                    )

                    out_path = (
                        out_dir / f"{rank:02d}_{_safe_output_stem(story)}.mp4"
                    )
                    srt_content = sub.get("srt") or None
                    durations = [float(d) for d in sub.get("durations") or []]

                    try:
                        add_overlays(
                            joined_local, story, transcript["segments"],
                            durations, work_dir, out_path,
                            bg_color, add_captions, input_images, video_title,
                            srt_content=srt_content,
                            upper_text_color=upper_text_color,
                            upper_edge_color=upper_edge_color,
                            subtitle_text_color=subtitle_text_color,
                            subtitle_edge_color=subtitle_edge_color,
                        )
                    except Exception as e:
                        print(f"[render_videos_job] story #{rank} overlay failed: {e}")
                        continue

                    output_paths.append(out_path)

            else:
                # ── Full mode (legacy single-shot) ──────────────────
                video_path = _download_video_from_r2(r2, job_id, tmp)
                output_paths = render_all_stories(
                    video_path, stories, transcript, out_dir,
                    max_duration=max_dur,
                    bg_color=bg_color,
                    add_captions=add_captions,
                    input_images=input_images,
                    video_title=video_title,
                    upper_text_color=upper_text_color,
                    upper_edge_color=upper_edge_color,
                    subtitle_text_color=subtitle_text_color,
                    subtitle_edge_color=subtitle_edge_color,
                )

            # ── Upload results + record in DB ────────────────────────
            for path in output_paths:
                r2_key = f"output/{path.name}"
                r2.save_file(path, r2_key)

                rank = int(path.stem.split("_")[0])
                story = next((s for s in stories if s["rank"] == rank), {})
                size_mb = path.stat().st_size / 1024 / 1024
                duration = get_clip_duration(path)

                db.save_output_video(
                    job_id=job_id,
                    rank=rank,
                    title=story.get("title", path.stem),
                    r2_url=r2_key,
                    duration_sec=duration,
                    file_size_mb=size_mb,
                )

                db.update_job_progress(
                    job_id, "rendering",
                    current=rank, total=len(stories),
                    detail=f"Rendered {path.name}",
                )

            db.update_job_status(
                job_id, "complete",
                progress={
                    "step": "complete",
                    "current": len(output_paths),
                    "total": len(stories),
                },
            )
            return {
                "job_id": job_id,
                "videos_rendered": len(output_paths),
                "total_stories": len(stories),
            }

    except Exception as e:
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise


# ── Web API Endpoints ────────────────────────────────────────────────
# FastAPI/Pydantic are imported INSIDE api() so the other Modal functions
# (transcribe_video_job, extract_and_render) don't need fastapi in their
# module-load path. Only the api() container ever imports fastapi.

@app.function(secrets=[secrets])
@modal.asgi_app()
def api():
    """ASGI endpoint serving the FastAPI web app.

    Called by the Next.js frontend via Vercel API routes.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    web_app = FastAPI()

    # Allow the Next.js frontend (Vercel + localhost) to call this API
    # directly from the browser. Browsers send a preflight OPTIONS for
    # POST with JSON bodies, which FastAPI won't answer without CORS.
    web_app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https://([a-z0-9-]+\.)*vercel\.app$|^http://localhost(:\d+)?$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=3600,
    )

    class SubmitJobRequest(BaseModel):
        job_id: str
        whisper_model: str = "medium"
        whisper_language: str = "ja"
        skip_proofread: bool = False

    class StartExtractRequest(BaseModel):
        """Phase 2a: called after transcript review, runs Claude extraction."""
        job_id: str
        num_stories: int = 10
        duration_preset: str = "30-60"

    class StartRenderRequest(BaseModel):
        """Phase 2b: called after story review, renders videos."""
        job_id: str
        duration_preset: str = "30-60"
        bg_color: str = "white"
        add_captions: bool = True

    class StartPrepareSubtitlesRequest(BaseModel):
        """Phase 2b-prep: run cut + join + SRT, pause for subtitle review.

        Only called when the user opted into --review-subtitles-equivalent
        behavior via the StoryEditor checkbox. ``bg_color`` and
        ``duration_preset`` are needed because the joined intermediate
        already has the background applied and must match what the final
        render step will overlay onto.
        """
        job_id: str
        duration_preset: str = "30-60"
        bg_color: str = "white"

    class UploadUrlRequest(BaseModel):
        job_id: str
        filename: str
        content_type: str = "video/mp4"
        # Sub-path inside the job folder. Use "source" for the main video
        # (default) or "input" for overlay images that should be composited
        # into the output.
        path_prefix: str = "source"

    class DownloadUrlRequest(BaseModel):
        """Request a presigned URL for downloading a rendered output video.

        The ``key`` is the value stored in ``output_videos.r2_url`` — which
        despite the column name is an R2 object key *relative to the job
        prefix* (e.g. ``"output/02_video.mp4"``), not an absolute URL.
        """
        job_id: str
        key: str
        filename: str = "video.mp4"

    class DeleteR2FilesRequest(BaseModel):
        """Delete every R2 object under ``jobs/<job_id>/``.

        The Supabase row is expected to have been deleted first via RLS
        from the browser — this endpoint only reclaims R2 storage. Since
        job IDs are UUIDs (2^122 entropy) we treat possession of the ID
        as sufficient authorization for cleanup; the worst-case abuse is
        an attacker guessing a UUID to free someone else's R2 storage.
        """
        job_id: str

    @web_app.post("/submit-job")
    async def submit_job(req: SubmitJobRequest):
        """Start Phase 1: transcribe video already in R2. Returns call_id.

        The video must already be uploaded to R2 at
        jobs/{job_id}/source/<filename>. Use POST /upload-url to get a
        presigned URL for uploading first.
        """
        call = await transcribe_video_job.spawn.aio(
            job_id=req.job_id,
            whisper_model=req.whisper_model,
            whisper_language=req.whisper_language,
            skip_proofread=req.skip_proofread,
        )
        return {"call_id": call.object_id, "job_id": req.job_id}

    @web_app.post("/start-extract")
    async def start_extract(req: StartExtractRequest):
        """Start Phase 2a: extract stories only (quick Claude call)."""
        call = await extract_stories_job.spawn.aio(
            job_id=req.job_id,
            num_stories=req.num_stories,
            duration_preset=req.duration_preset,
        )
        return {"call_id": call.object_id, "job_id": req.job_id}

    @web_app.post("/start-render")
    async def start_render(req: StartRenderRequest):
        """Start Phase 2b: render videos from (possibly edited) stories.

        If ``stories.subtitles_json`` exists (populated by
        ``/start-prepare-subtitles``), render_videos_job skips cutting
        and only overlays the stored SRT — the user's edits from the
        SubtitleEditor are picked up from the DB.
        """
        call = await render_videos_job.spawn.aio(
            job_id=req.job_id,
            duration_preset=req.duration_preset,
            bg_color=req.bg_color,
            add_captions=req.add_captions,
        )
        return {"call_id": call.object_id, "job_id": req.job_id}

    @web_app.post("/start-prepare-subtitles")
    async def start_prepare_subtitles(req: StartPrepareSubtitlesRequest):
        """Kick off the prepare-subtitles phase (cut + join + SRT).

        Flips the job to ``preparing_subtitles`` → ``awaiting_subtitle_review``
        so the frontend can open the SubtitleEditor.
        """
        call = await prepare_subtitles_job.spawn.aio(
            job_id=req.job_id,
            duration_preset=req.duration_preset,
            bg_color=req.bg_color,
        )
        return {"call_id": call.object_id, "job_id": req.job_id}

    @web_app.post("/upload-url")
    async def get_upload_url(req: UploadUrlRequest):
        """Get a presigned URL for uploading a file to R2."""
        from mkvideo.storage.r2 import R2Storage

        # Whitelist sub-paths to avoid unexpected keys
        sub = req.path_prefix.strip("/").lower()
        if sub not in ("source", "input"):
            raise HTTPException(
                status_code=400,
                detail="path_prefix must be 'source' or 'input'",
            )

        r2 = R2Storage(prefix=f"jobs/{req.job_id}")
        sub_key = f"{sub}/{req.filename}"
        upload_url = r2.generate_upload_url(
            sub_key,
            expires_in=3600,
            content_type=req.content_type,
        )
        return {
            "upload_url": upload_url,
            "r2_key": f"jobs/{req.job_id}/{sub_key}",
        }

    @web_app.post("/download-url")
    async def get_download_url(req: DownloadUrlRequest):
        """Return presigned GET URLs for playing or downloading a video.

        Returns two URLs for the same object:
        - ``view_url``: inline disposition — safe to set on ``<video src>``.
        - ``download_url``: attachment disposition with the requested
          ``filename`` so the browser saves it with a useful name instead
          of the R2 key.
        """
        from mkvideo.storage.r2 import R2Storage

        # Normalize: tolerate callers that accidentally pass either a
        # leading slash or the full ``jobs/<id>/`` prefix.
        rel = req.key.lstrip("/")
        job_prefix = f"jobs/{req.job_id}/"
        if rel.startswith(job_prefix):
            rel = rel[len(job_prefix):]

        r2 = R2Storage(prefix=f"jobs/{req.job_id}")
        full_key = r2._key(rel)

        # RFC 6266: quote the filename and strip characters that would
        # break the header. Browsers fall back to the URL basename if the
        # header is malformed, so we prefer safety over fidelity.
        safe_name = req.filename.replace('"', "").replace("\\", "").replace("\n", "")
        if not safe_name:
            safe_name = "video.mp4"

        view_url = r2.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": r2.bucket_name, "Key": full_key},
            ExpiresIn=3600,
        )
        download_url = r2.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": r2.bucket_name,
                "Key": full_key,
                "ResponseContentDisposition": f'attachment; filename="{safe_name}"',
            },
            ExpiresIn=3600,
        )
        return {"view_url": view_url, "download_url": download_url}

    @web_app.post("/delete-r2-files")
    async def delete_r2_files(req: DeleteR2FilesRequest):
        """Best-effort cleanup of all R2 objects under ``jobs/<job_id>/``.

        Returns the count of deleted objects. Safe to call on a prefix
        that no longer exists (paginator yields nothing).
        """
        from mkvideo.storage.r2 import R2Storage

        r2 = R2Storage(prefix=f"jobs/{req.job_id}")
        # R2Storage._key("") returns "jobs/<id>/" (list-prefix form).
        prefix = r2._key("")
        if not prefix.endswith("/"):
            prefix += "/"

        paginator = r2.s3.get_paginator("list_objects_v2")
        total_deleted = 0
        for page in paginator.paginate(Bucket=r2.bucket_name, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            # S3 delete_objects caps at 1000 keys per call — paginator
            # pages are already bounded to 1000 by default.
            keys = [{"Key": obj["Key"]} for obj in objects]
            r2.s3.delete_objects(
                Bucket=r2.bucket_name,
                Delete={"Objects": keys, "Quiet": True},
            )
            total_deleted += len(keys)

        return {"deleted": total_deleted, "prefix": prefix}

    @web_app.get("/job-status/{call_id}")
    async def job_status(call_id: str):
        """Check if a Modal function call has completed."""
        try:
            call = modal.FunctionCall.from_id(call_id)
            result = call.get(timeout=0)
            return {"status": "completed", "result": result}
        except TimeoutError:
            return {"status": "running"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @web_app.get("/r2-usage")
    async def r2_usage():
        """Return R2 bucket usage: total bytes, object count, and free-tier percent.

        Paginates through all objects in the bucket (not scoped to a job prefix)
        so the dashboard can show overall storage consumption.
        """
        from mkvideo.storage.r2 import R2Storage

        r2 = R2Storage(prefix="")
        paginator = r2.s3.get_paginator("list_objects_v2")
        total_bytes = 0
        total_objects = 0
        for page in paginator.paginate(Bucket=r2.bucket_name):
            for obj in page.get("Contents", []):
                total_objects += 1
                total_bytes += int(obj.get("Size", 0))

        free_limit_bytes = 10_000_000_000  # Cloudflare R2 free tier: 10 GB
        usage_percent = round(total_bytes / free_limit_bytes * 100, 2) if free_limit_bytes else 0.0
        return {
            "bucket": r2.bucket_name,
            "total_bytes": total_bytes,
            "total_objects": total_objects,
            "free_limit_bytes": free_limit_bytes,
            "usage_percent": usage_percent,
        }

    return web_app
