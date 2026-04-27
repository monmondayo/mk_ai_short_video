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
--
-- 冪等性について: このポリシーは事後的に 001 と 002 にも forward-port
-- 済みのため、`supabase db reset` 等で 001→002→005 の順に流すと
-- このファイルでの create 時点で既に存在していて重複エラーになる。
-- drop policy if exists で一度消してから作り直すことで、
--   (a) 既存 prod DB (005 を初めて流すケース)
--   (b) 新規 DB (001/002 が先に作ったケース)
-- の両方で冪等に通るようにする。
-- ============================================================

drop policy if exists "Users can update own stories" on public.stories;

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
