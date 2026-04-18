-- ============================================================
-- mkvideo Supabase Schema Migration
-- ============================================================
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- or via supabase CLI: supabase db push
-- ============================================================

-- ── Enable UUID extension ───────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── 1. jobs ─────────────────────────────────────────────────
-- Master table for each video processing job.
-- Frontend subscribes to Realtime on `progress` JSONB changes.
create table if not exists public.jobs (
    id            uuid primary key default uuid_generate_v4(),
    user_id       uuid not null references auth.users(id) on delete cascade,
    youtube_url   text not null,
    video_title   text default '',
    status        text not null default 'pending'
                  check (status in (
                      'pending',                -- job created, waiting for video upload
                      'uploading',              -- video being uploaded to R2
                      'downloading',            -- (legacy) downloading from R2
                      'transcribing',           -- Whisper running
                      'proofreading',           -- Claude proofreading transcript
                      'awaiting_review',        -- transcript ready, waiting for user
                      'awaiting_story_review',  -- stories extracted, waiting for user
                      'preparing_subtitles',    -- cutting + joining + generating SRT
                      'awaiting_subtitle_review', -- SRTs ready, waiting for user
                      'extracting',             -- Claude extracting stories
                      'rendering',              -- ffmpeg rendering shorts
                      'complete',               -- all done
                      'failed'                  -- error occurred
                  )),
    progress      jsonb default '{}',
    -- progress schema: { "step": "transcribing", "current": 2, "total": 10, "detail": "..." }

    -- Job parameters
    whisper_model     text default 'medium',
    whisper_language  text default 'ja',
    num_stories       int default 10,
    duration_preset   text default '30-60',
    bg_color          text default 'white',
    add_captions      boolean default true,
    skip_proofread    boolean default false,

    -- R2 storage
    r2_video_key      text default '',     -- R2 key for source video

    -- Error handling
    error_message     text default '',
    modal_call_id     text default '',     -- Modal function call ID for polling

    -- Timestamps
    created_at    timestamptz default now(),
    updated_at    timestamptz default now()
);

-- Index for user's job list (ordered by newest first)
create index if not exists idx_jobs_user_id on public.jobs(user_id, created_at desc);

-- Index for status filtering
create index if not exists idx_jobs_status on public.jobs(status);


-- ── 2. transcripts ──────────────────────────────────────────
-- Stores the Whisper transcript (after optional proofreading).
-- The `segments` JSONB array matches Whisper's output format and
-- is editable by the user in the web transcript editor.
create table if not exists public.transcripts (
    id                  uuid primary key default uuid_generate_v4(),
    job_id              uuid not null references public.jobs(id) on delete cascade,
    segments            jsonb not null default '[]',
    -- segments schema: [{ "start": 0.0, "end": 2.5, "text": "...", "words": [...] }, ...]

    raw_json_url        text default '',       -- R2 key for raw Whisper output
    proofread_json_url  text default '',       -- R2 key for proofread version

    created_at          timestamptz default now(),
    updated_at          timestamptz default now(),

    unique(job_id)  -- one transcript per job
);

create index if not exists idx_transcripts_job_id on public.transcripts(job_id);


-- ── 3. stories ──────────────────────────────────────────────
-- Stores the Claude-extracted story definitions.
-- Contains segment ranges, transition types, hook text, etc.
create table if not exists public.stories (
    id              uuid primary key default uuid_generate_v4(),
    job_id          uuid not null references public.jobs(id) on delete cascade,
    stories_json    jsonb not null default '[]',
    -- stories_json schema: [{ "rank": 1, "title": "...", "hook_text": "...",
    --   "segments": [{ "start": 10.5, "end": 25.0, "transition_in": "dissolve" }],
    --   ... }, ...]

    subtitles_json  jsonb default null,
    -- subtitles_json schema (present only when the user opted into
    -- subtitle review, mirroring the CLI's --review-subtitles flag):
    --   { "<rank>": { "srt": "...", "durations": [..],
    --                 "joined_key": "joined/s<rank>.mp4" }, ... }

    created_at      timestamptz default now(),
    updated_at      timestamptz default now(),

    unique(job_id)  -- one story set per job
);

create index if not exists idx_stories_job_id on public.stories(job_id);


-- ── 4. output_videos ────────────────────────────────────────
-- One row per rendered short video. Links to R2 file.
create table if not exists public.output_videos (
    id              uuid primary key default uuid_generate_v4(),
    job_id          uuid not null references public.jobs(id) on delete cascade,
    rank            int not null,
    title           text default '',
    r2_url          text not null,          -- R2 key for the mp4 file
    duration_sec    float default 0,
    file_size_mb    float default 0,

    created_at      timestamptz default now(),

    unique(job_id, rank)
);

create index if not exists idx_output_videos_job_id on public.output_videos(job_id);


-- ============================================================
-- Row Level Security (RLS)
-- ============================================================
-- Users can only access their own data.
-- Modal workers use the service_role key which bypasses RLS.

alter table public.jobs enable row level security;
alter table public.transcripts enable row level security;
alter table public.stories enable row level security;
alter table public.output_videos enable row level security;

-- ── Jobs RLS ────────────────────────────────────────────────
-- Users can read their own jobs
create policy "Users can view own jobs"
    on public.jobs for select
    using (auth.uid() = user_id);

-- Users can insert jobs (user_id must match)
create policy "Users can create jobs"
    on public.jobs for insert
    with check (auth.uid() = user_id);

-- Users can update their own jobs (e.g., edit parameters before submit)
create policy "Users can update own jobs"
    on public.jobs for update
    using (auth.uid() = user_id);

-- Users can delete their own jobs
create policy "Users can delete own jobs"
    on public.jobs for delete
    using (auth.uid() = user_id);

-- ── Transcripts RLS ─────────────────────────────────────────
-- Users can access transcripts for their own jobs
create policy "Users can view own transcripts"
    on public.transcripts for select
    using (
        exists (
            select 1 from public.jobs
            where jobs.id = transcripts.job_id
              and jobs.user_id = auth.uid()
        )
    );

-- Users can update transcripts (for the transcript editor)
create policy "Users can update own transcripts"
    on public.transcripts for update
    using (
        exists (
            select 1 from public.jobs
            where jobs.id = transcripts.job_id
              and jobs.user_id = auth.uid()
        )
    );

-- ── Stories RLS ─────────────────────────────────────────────
create policy "Users can view own stories"
    on public.stories for select
    using (
        exists (
            select 1 from public.jobs
            where jobs.id = stories.job_id
              and jobs.user_id = auth.uid()
        )
    );

-- ── Output Videos RLS ───────────────────────────────────────
create policy "Users can view own output videos"
    on public.output_videos for select
    using (
        exists (
            select 1 from public.jobs
            where jobs.id = output_videos.job_id
              and jobs.user_id = auth.uid()
        )
    );


-- ============================================================
-- Realtime (for live progress updates to frontend)
-- ============================================================
-- Enable Realtime on the jobs table so the frontend can subscribe
-- to changes on the `progress` and `status` columns.

alter publication supabase_realtime add table public.jobs;


-- ============================================================
-- Helper function: auto-update updated_at on row changes
-- ============================================================
create or replace function public.update_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger tr_jobs_updated_at
    before update on public.jobs
    for each row execute function public.update_updated_at();

create trigger tr_transcripts_updated_at
    before update on public.transcripts
    for each row execute function public.update_updated_at();

create trigger tr_stories_updated_at
    before update on public.stories
    for each row execute function public.update_updated_at();
