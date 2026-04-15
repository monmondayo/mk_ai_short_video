"""
Modal serverless deployment for mkvideo.

Two main functions:
  1. download_and_transcribe - Download YouTube video, run Whisper, proofread
  2. extract_and_render - Extract stories with Claude, render all short videos

Web API endpoints:
  POST /submit-job       - Start a new job (returns job_id + call_id)
  POST /start-render     - Start phase 2 after transcript review
  GET  /job-status/{id}  - Poll job status

Deploy:
  modal deploy mkvideo/cloud/modal_app.py

Prerequisites:
  modal secret create mkvideo-secrets \\
    ANTHROPIC_API_KEY=sk-ant-... \\
    SUPABASE_URL=https://xxx.supabase.co \\
    SUPABASE_SERVICE_KEY=eyJ... \\
    R2_ACCOUNT_ID=... \\
    R2_ACCESS_KEY_ID=... \\
    R2_SECRET_ACCESS_KEY=... \\
    R2_BUCKET_NAME=mkvideo
"""

import modal

# ── Container image ──────────────────────────────────────────────────
# Installs ffmpeg, CJK fonts, and all Python dependencies.
# Whisper model weights are cached in a persistent Volume.
mkvideo_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "fonts-noto-cjk")
    .pip_install(
        "yt-dlp>=2024.1.1",
        "openai-whisper>=20231117",
        "anthropic>=0.20.0",
        "python-dotenv>=1.0.0",
        "Pillow>=10.0.0",
        "Janome>=0.5.0",
        "boto3>=1.34.0",
        "requests>=2.31.0",
    )
)

app = modal.App("mkvideo", image=mkvideo_image)

# Persistent volume for Whisper model weights (avoids re-download on cold start)
whisper_cache = modal.Volume.from_name("mkvideo-whisper-cache", create_if_missing=True)

# Shared secret for all API keys
secrets = modal.Secret.from_name("mkvideo-secrets")

# ── Constants ────────────────────────────────────────────────────────
WHISPER_CACHE_PATH = "/cache/whisper"


# ── Job 1: Download + Transcribe ─────────────────────────────────────
@app.function(
    secrets=[secrets],
    volumes={WHISPER_CACHE_PATH: whisper_cache},
    gpu="T4",       # GPU for Whisper; use gpu=None for base/tiny models on CPU
    timeout=3600,   # 1 hour max
    memory=16384,   # 16 GB RAM
)
def download_and_transcribe(
    job_id: str,
    youtube_url: str,
    whisper_model: str = "medium",
    whisper_language: str = "ja",
    skip_proofread: bool = False,
) -> dict:
    """Phase 1: Download video, transcribe with Whisper, proofread with Claude.

    Updates job status in Supabase at each step.
    Uploads results to R2.
    Returns dict with transcript data and R2 keys.
    """
    import os
    import tempfile
    from pathlib import Path

    # Set Whisper cache dir to persistent volume
    os.environ["XDG_CACHE_HOME"] = WHISPER_CACHE_PATH

    from mkvideo.cloud.supabase_client import SupabaseJobClient
    from mkvideo.pipeline.download import download_video, fetch_video_title
    from mkvideo.pipeline.transcribe import proofread_transcript, transcribe_video
    from mkvideo.storage.r2 import R2Storage

    db = SupabaseJobClient()
    r2 = R2Storage(prefix=f"jobs/{job_id}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Step 1: Download
            db.update_job_status(job_id, "downloading")
            video_path, video_title = download_video(youtube_url, tmp)

            # Upload source video to R2
            r2_video_key = f"source/{video_path.name}"
            r2.save_file(video_path, r2_video_key)
            db.update_job_progress(job_id, "downloading", 1, 1, "Download complete")

            # Step 2: Transcribe
            db.update_job_status(job_id, "transcribing")
            language = whisper_language if whisper_language else None
            transcript = transcribe_video(video_path, model_name=whisper_model, language=language)

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
                "video_title": video_title,
                "r2_video_key": r2_video_key,
                "segment_count": len(transcript["segments"]),
            }

    except Exception as e:
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise


# ── Job 2: Extract Stories + Render ──────────────────────────────────
@app.function(
    secrets=[secrets],
    timeout=7200,   # 2 hours max (rendering is slow)
    memory=8192,    # 8 GB RAM
    cpu=4.0,        # 4 vCPUs for ffmpeg
)
def extract_and_render(
    job_id: str,
    youtube_url: str,
    num_stories: int = 10,
    duration_preset: str = "30-60",
    bg_color: str = "white",
    add_captions: bool = True,
) -> dict:
    """Phase 2: Extract stories with Claude, render all short videos.

    Called after user reviews/edits transcript.
    Downloads source video from R2, renders, uploads results.
    """
    import tempfile
    from pathlib import Path

    from mkvideo.cloud.supabase_client import SupabaseJobClient
    from mkvideo.pipeline.constants import (
        DURATION_PRESETS,
        DURATION_TOLERANCE_SEC,
        estimate_joined_duration,
    )
    from mkvideo.pipeline.render import (
        cleanup_render_dir,
        get_video_info,
        render_all_stories,
    )
    from mkvideo.pipeline.stories import enforce_story_duration_range, extract_stories
    from mkvideo.storage.r2 import R2Storage

    db = SupabaseJobClient()
    r2 = R2Storage(prefix=f"jobs/{job_id}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / "output"
            out_dir.mkdir()

            # Get job info
            job = db.get_job(job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} not found")

            # Download source video from R2
            db.update_job_status(job_id, "extracting")
            r2_video_key = f"source/"
            # Find the video file in R2
            video_path = tmp / "source.mp4"
            # List objects to find the video
            prefix_key = r2._key("source/")
            response = r2.s3.list_objects_v2(
                Bucket=r2.bucket_name, Prefix=prefix_key, MaxKeys=10
            )
            video_key = None
            for obj in response.get("Contents", []):
                if obj["Key"].endswith(".mp4"):
                    video_key = obj["Key"]
                    break
            if not video_key:
                raise RuntimeError("Source video not found in R2")
            r2.s3.download_file(r2.bucket_name, video_key, str(video_path))

            # Get transcript from Supabase
            transcript_data = db.get_transcript(job_id)
            if not transcript_data:
                raise RuntimeError("Transcript not found")
            transcript = {
                "text": "".join(s["text"] for s in transcript_data["segments"]),
                "segments": transcript_data["segments"],
            }

            # Get video info
            info = get_video_info(video_path)

            # Extract stories
            min_dur, max_dur = DURATION_PRESETS.get(duration_preset, (30, 60))
            stories = extract_stories(
                transcript,
                num_stories=num_stories,
                video_duration=info["duration"],
                min_dur=min_dur,
                max_dur=max_dur,
            )

            # Normalize durations
            enforce_story_duration_range(
                stories,
                min_total=min_dur,
                max_total=max_dur,
                video_duration=info["duration"],
                tolerance_sec=DURATION_TOLERANCE_SEC,
            )

            # Save stories to Supabase
            db.save_stories(job_id, stories)

            # Render
            db.update_job_status(job_id, "rendering")

            # Override TEMP_DIR to use tmpdir
            import mkvideo.pipeline.constants as constants
            constants.TEMP_DIR = tmp / "temp"
            constants.TEMP_DIR.mkdir()
            constants.INPUT_DIR = tmp / "input"
            constants.INPUT_DIR.mkdir()

            # TODO: Download input images from R2 if user uploaded any
            input_images = []

            video_title = job.get("youtube_url", "")  # Could be improved

            output_paths = render_all_stories(
                video_path, stories, transcript, out_dir,
                max_duration=max_dur,
                bg_color=bg_color,
                add_captions=add_captions,
                input_images=input_images,
                video_title=video_title,
            )

            # Upload finished videos to R2 and record in Supabase
            for path in output_paths:
                r2_key = f"output/{path.name}"
                r2.save_file(path, r2_key)

                # Extract rank from filename (e.g., "01_title.mp4" -> 1)
                rank = int(path.stem.split("_")[0])
                story = next((s for s in stories if s["rank"] == rank), {})
                size_mb = path.stat().st_size / 1024 / 1024

                # Get video duration
                from mkvideo.pipeline.render import get_clip_duration
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

            db.update_job_status(job_id, "complete",
                                progress={"step": "complete",
                                          "current": len(output_paths),
                                          "total": len(stories)})

            return {
                "job_id": job_id,
                "videos_rendered": len(output_paths),
                "total_stories": len(stories),
            }

    except Exception as e:
        db.update_job_status(job_id, "failed", error_message=str(e))
        raise


# ── Web API Endpoints ────────────────────────────────────────────────
# These are called by the Next.js frontend via Vercel API routes.

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

web_app = FastAPI()


class SubmitJobRequest(BaseModel):
    job_id: str
    youtube_url: str
    whisper_model: str = "medium"
    whisper_language: str = "ja"
    skip_proofread: bool = False


class StartRenderRequest(BaseModel):
    job_id: str
    youtube_url: str
    num_stories: int = 10
    duration_preset: str = "30-60"
    bg_color: str = "white"
    add_captions: bool = True


@web_app.post("/submit-job")
async def submit_job(req: SubmitJobRequest):
    """Start Phase 1: download + transcribe. Returns immediately with call_id."""
    call = await download_and_transcribe.spawn.aio(
        job_id=req.job_id,
        youtube_url=req.youtube_url,
        whisper_model=req.whisper_model,
        whisper_language=req.whisper_language,
        skip_proofread=req.skip_proofread,
    )
    return {"call_id": call.object_id, "job_id": req.job_id}


@web_app.post("/start-render")
async def start_render(req: StartRenderRequest):
    """Start Phase 2: extract stories + render. Called after transcript review."""
    call = await extract_and_render.spawn.aio(
        job_id=req.job_id,
        youtube_url=req.youtube_url,
        num_stories=req.num_stories,
        duration_preset=req.duration_preset,
        bg_color=req.bg_color,
        add_captions=req.add_captions,
    )
    return {"call_id": call.object_id, "job_id": req.job_id}


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


@app.function(secrets=[secrets])
@modal.asgi_app()
def api():
    """ASGI endpoint serving the FastAPI web app."""
    return web_app
