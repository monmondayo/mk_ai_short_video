-- ============================================================
-- mkvideo: 既存テーブルを DROP して新スキーマで再作成
-- ============================================================
-- Supabase Dashboard → SQL Editor → New Query → 貼り付け → Run
-- ⚠️ 既存データはすべて削除されます (開発環境専用)
-- ============================================================

-- ── Step 0: Realtime publication から既存テーブルを除外 ──────
-- (これをしないと DROP CASCADE が失敗する場合がある)
do $$
begin
    execute 'alter publication supabase_realtime drop table public.jobs';
exception when others then
    raise notice 'jobs was not in publication, skipping';
end;
$$;

-- ── Step 1: 既存テーブルを削除 ──────────────────────────────
drop table if exists public.output_videos;
drop table if exists public.stories;
drop table if exists public.transcripts;
drop table if exists public.jobs;

-- 既存のトリガー関数も削除
drop function if exists public.update_updated_at();

-- ── Step 2: UUID 拡張を有効化 ───────────────────────────────
create extension if not exists "uuid-ossp";

-- ── Step 3: テーブル作成 ────────────────────────────────────

-- 3-1. jobs (マスターテーブル)
create table public.jobs (
    id            uuid primary key default uuid_generate_v4(),
    user_id       uuid not null,  -- Supabase Auth の auth.uid() と一致させる
    youtube_url   text not null,
    video_title   text default '',
    status        text not null default 'pending'
                  check (status in (
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
                  )),
    progress      jsonb default '{}',

    -- ジョブパラメータ
    whisper_model     text default 'medium',
    whisper_language  text default 'ja',
    num_stories       int default 10,
    duration_preset   text default '30-60',
    bg_color          text default 'white',
    add_captions      boolean default true,
    skip_proofread    boolean default false,

    -- R2 ストレージ
    r2_video_key      text default '',

    -- エラー / Modal連携
    error_message     text default '',
    modal_call_id     text default '',

    -- タイムスタンプ
    created_at    timestamptz default now(),
    updated_at    timestamptz default now()
);

create index idx_jobs_user_id on public.jobs(user_id, created_at desc);
create index idx_jobs_status on public.jobs(status);

-- 3-2. transcripts (文字起こし)
create table public.transcripts (
    id                  uuid primary key default uuid_generate_v4(),
    job_id              uuid not null references public.jobs(id) on delete cascade,
    segments            jsonb not null default '[]',
    raw_json_url        text default '',
    proofread_json_url  text default '',
    created_at          timestamptz default now(),
    updated_at          timestamptz default now(),
    unique(job_id)
);

create index idx_transcripts_job_id on public.transcripts(job_id);

-- 3-3. stories (ストーリー定義)
create table public.stories (
    id              uuid primary key default uuid_generate_v4(),
    job_id          uuid not null references public.jobs(id) on delete cascade,
    stories_json    jsonb not null default '[]',
    -- subtitles_json is populated only when the user opted into
    -- --review-subtitles style pause before rendering.
    subtitles_json  jsonb default null,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now(),
    unique(job_id)
);

create index idx_stories_job_id on public.stories(job_id);

-- 3-4. output_videos (完成動画)
create table public.output_videos (
    id              uuid primary key default uuid_generate_v4(),
    job_id          uuid not null references public.jobs(id) on delete cascade,
    rank            int not null,
    title           text default '',
    r2_url          text not null,
    duration_sec    float default 0,
    file_size_mb    float default 0,
    created_at      timestamptz default now(),
    unique(job_id, rank)
);

create index idx_output_videos_job_id on public.output_videos(job_id);

-- ── Step 4: Row Level Security (RLS) ────────────────────────

alter table public.jobs enable row level security;
alter table public.transcripts enable row level security;
alter table public.stories enable row level security;
alter table public.output_videos enable row level security;

-- Jobs
create policy "Users can view own jobs"
    on public.jobs for select
    using (auth.uid() = user_id);

create policy "Users can create jobs"
    on public.jobs for insert
    with check (auth.uid() = user_id);

create policy "Users can update own jobs"
    on public.jobs for update
    using (auth.uid() = user_id);

create policy "Users can delete own jobs"
    on public.jobs for delete
    using (auth.uid() = user_id);

-- Transcripts
create policy "Users can view own transcripts"
    on public.transcripts for select
    using (exists (
        select 1 from public.jobs
        where jobs.id = transcripts.job_id and jobs.user_id = auth.uid()
    ));

create policy "Users can update own transcripts"
    on public.transcripts for update
    using (exists (
        select 1 from public.jobs
        where jobs.id = transcripts.job_id and jobs.user_id = auth.uid()
    ));

-- Stories
create policy "Users can view own stories"
    on public.stories for select
    using (exists (
        select 1 from public.jobs
        where jobs.id = stories.job_id and jobs.user_id = auth.uid()
    ));

-- UPDATE policy is required so the browser-side StoryEditor and
-- SubtitleEditor can persist edits. Without it RLS silently drops the
-- update (0 rows affected, no error) and the user's changes disappear.
create policy "Users can update own stories"
    on public.stories for update
    using (exists (
        select 1 from public.jobs
        where jobs.id = stories.job_id and jobs.user_id = auth.uid()
    ))
    with check (exists (
        select 1 from public.jobs
        where jobs.id = stories.job_id and jobs.user_id = auth.uid()
    ));

-- Output Videos
create policy "Users can view own output videos"
    on public.output_videos for select
    using (exists (
        select 1 from public.jobs
        where jobs.id = output_videos.job_id and jobs.user_id = auth.uid()
    ));

-- ── Step 5: Realtime ────────────────────────────────────────
alter publication supabase_realtime add table public.jobs;

-- ── Step 6: updated_at 自動更新トリガー ─────────────────────
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

-- ============================================================
-- ✅ 完了！ 4テーブル作成済み (RLS有効, Realtime有効)
-- ============================================================
