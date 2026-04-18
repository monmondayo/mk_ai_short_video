-- ============================================================
-- mkvideo: add subtitle review step
-- ============================================================
-- Mirrors the local CLI's --review-subtitles flag. Between story
-- extraction and final rendering we now optionally pause so the
-- user can edit per-story SRT captions in the web UI.
--
-- New statuses:
--   preparing_subtitles       — Modal is cutting + joining + generating SRT
--   awaiting_subtitle_review  — SRTs are in stories.subtitles_json, waiting
--                               for the user to edit and confirm
--
-- New column:
--   stories.subtitles_json    — keyed by rank string:
--     {
--       "1": {
--         "srt": "1\n00:00:00,000 --> ...\n...",
--         "durations": [10.5, 8.2],       -- per-segment duration after cut
--         "joined_key": "joined/s1.mp4"   -- R2 key (relative to jobs/<id>/)
--                                            for the already-joined video
--       },
--       ...
--     }
--
-- Absence of the column (or a null value) signals the legacy single-shot
-- render flow (no subtitle review pause).
-- ============================================================

-- ── Extend the jobs.status CHECK ────────────────────────────
alter table public.jobs drop constraint if exists jobs_status_check;

alter table public.jobs
  add constraint jobs_status_check check (status in (
    'pending',
    'uploading',
    'downloading',
    'transcribing',
    'proofreading',
    'awaiting_review',
    'awaiting_story_review',
    'preparing_subtitles',
    'awaiting_subtitle_review',
    'extracting',
    'rendering',
    'complete',
    'failed'
  ));

-- ── Add stories.subtitles_json (nullable; absent = legacy flow) ───
alter table public.stories
  add column if not exists subtitles_json jsonb default null;

-- Optional GIN index if we ever want to query inside this JSONB;
-- skipped for now — the only access pattern is "fetch the whole blob
-- for this job_id", which the existing job_id index already supports.
