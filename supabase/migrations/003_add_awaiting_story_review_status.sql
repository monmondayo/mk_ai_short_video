-- ============================================================
-- mkvideo: jobs.status の CHECK 制約に 'awaiting_story_review' を追加
-- ============================================================
-- 背景: Modal バックエンド (modal_app.py:308) は stories 抽出完了時に
--       status = 'awaiting_story_review' を書き込む。しかし既存の
--       CHECK 制約にこの値が無いため、PostgREST が 400 Bad Request を
--       返し、extract_stories_job が最後の 1 行で失敗してジョブが
--       `failed` に落ちる。この制約を差し替えて復旧させる。
--
-- Supabase Dashboard → SQL Editor → New Query → 貼り付け → Run
-- ============================================================

-- 旧 CHECK 制約を削除し、'awaiting_story_review' を含む新しいものに差し替え。
-- 制約名は Supabase が自動生成する `jobs_status_check` がデフォルト。
-- もし手動で別名にしていた場合はこの DROP で失敗するので、その場合は
-- psql で `\d public.jobs` を見て実際の制約名を確認してから調整すること。
alter table public.jobs
    drop constraint if exists jobs_status_check;

alter table public.jobs
    add constraint jobs_status_check
    check (status in (
        'pending',
        'uploading',
        'downloading',
        'transcribing',
        'proofreading',
        'awaiting_review',
        'awaiting_story_review',
        'extracting',
        'rendering',
        'complete',
        'failed'
    ));
