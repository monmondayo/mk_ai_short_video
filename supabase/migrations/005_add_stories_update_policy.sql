-- ============================================================
-- stories テーブルに UPDATE ポリシーを追加
--
-- 問題: 002_drop_and_recreate.sql で作成した stories テーブルには
-- SELECT ポリシーしか無く、UPDATE ポリシーが欠落していた。
-- RLS 有効時は default-deny なので、ブラウザから .update() しても
-- PostgREST が 0 rows affected で静かに成功 (= エラーを返さない)
-- → フロントの StoryEditor / SubtitleEditor は「Saved」と表示するが
-- DB は変更されず、ユーザーの編集 (タイトル・フック・SRT) は
-- 全て破棄されていた。
--
-- transcripts には UPDATE ポリシーがあるため TranscriptEditor は
-- 正常に動作していた。このマイグレーションで stories を同じ形に
-- 揃える。
-- ============================================================

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
