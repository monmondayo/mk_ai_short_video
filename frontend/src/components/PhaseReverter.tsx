"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";

/**
 * Lets the user rewind a job to an earlier review phase without
 * re-uploading the source video or re-running expensive steps. Relies on
 * the fact that every prior artifact is already persisted:
 *
 * - ``transcript.segments`` survives re-runs → awaiting_review.
 * - ``stories.stories_json`` survives re-runs → awaiting_story_review.
 * - ``stories.subtitles_json`` + R2 joined/s*.mp4 survives → awaiting_subtitle_review.
 *
 * No cleanup of ``output_videos`` is needed because ``save_output_video``
 * upserts on (job_id, rank), so a re-render overwrites cleanly.
 *
 * Only shown while the job is NOT currently processing — letting the
 * user flip status mid-render would race the Modal worker.
 */

type Phase = {
  status: string;
  label: string;
  description: string;
  available: boolean;
};

export default function PhaseReverter({
  jobId,
  currentStatus,
  hasTranscript,
  hasStories,
  hasSubtitles,
}: {
  jobId: string;
  currentStatus: string;
  hasTranscript: boolean;
  hasStories: boolean;
  hasSubtitles: boolean;
}) {
  const router = useRouter();
  const supabase = createClient();
  const [reverting, setReverting] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const phases: Phase[] = [
    {
      status: "awaiting_review",
      label: "文字起こしレビュー",
      description:
        "Whisper の文字起こし結果を編集してから、もう一度ストーリー抽出を走らせます。",
      available: hasTranscript,
    },
    {
      status: "awaiting_story_review",
      label: "ストーリーレビュー",
      description:
        "抽出されたストーリーのタイトル・フックを編集してから、もう一度レンダリングします。",
      available: hasStories,
    },
    {
      status: "awaiting_subtitle_review",
      label: "字幕レビュー",
      description:
        "生成された SRT 字幕を編集してから、カット済み動画にオーバーレイのみ再適用します（最速）。",
      available: hasSubtitles,
    },
  ];

  // Only show phases the user can actually reach, and hide the one we're
  // already in (no-op).
  const visible = phases.filter(
    (p) => p.available && p.status !== currentStatus,
  );
  if (visible.length === 0) return null;

  const handleRevert = async (phase: Phase) => {
    const ok = window.confirm(
      `ジョブを「${phase.label}」に戻しますか？\n\n` +
        `・動画の再アップロードや文字起こしの再実行は不要です。\n` +
        `・以前のレンダリング結果は残りますが、再実行すれば上書きされます。`,
    );
    if (!ok) return;

    setErrorMsg(null);
    setReverting(phase.status);
    try {
      // .select() so we can detect RLS silent-fails. Also reset
      // error_message and progress so the UI doesn't show stale
      // completion/failure state from the previous run.
      const { data, error } = await supabase
        .from("jobs")
        .update({
          status: phase.status,
          progress: {
            step: phase.status,
            detail: "前のフェーズに戻しました",
          },
          error_message: null,
        })
        .eq("id", jobId)
        .select("id");

      if (error) throw new Error(error.message);
      if (!data || data.length === 0) {
        throw new Error(
          "No rows updated — check RLS UPDATE policy on jobs",
        );
      }

      router.refresh();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Failed to revert phase");
    } finally {
      // Always clear the busy state — otherwise a successful revert leaves
      // `reverting` stuck on the just-completed phase, and every other
      // phase button stays disabled (via `busy = reverting !== null`) until
      // a full page reload. router.refresh() re-fetches server data but
      // does NOT unmount this component, so useState is preserved.
      setReverting(null);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-900">
        前のフェーズに戻る
      </h3>
      <p className="text-xs text-gray-500 mt-1 mb-3">
        動画の再アップロードや重い処理のやり直しをせずに、
        過去のレビュー画面に戻って編集し直せます。
      </p>

      {errorMsg && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700 mb-3">
          {errorMsg}
        </div>
      )}

      <ul className="space-y-2">
        {visible.map((phase) => {
          const busy = reverting !== null;
          const isActive = reverting === phase.status;
          return (
            <li
              key={phase.status}
              className="flex items-start justify-between gap-3 border border-gray-100 rounded-lg px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900">
                  {phase.label}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {phase.description}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleRevert(phase)}
                disabled={busy}
                className="shrink-0 px-3 py-1.5 bg-gray-900 text-white rounded-md text-sm font-medium hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
              >
                {isActive && (
                  <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                )}
                {isActive ? "戻しています..." : "戻る"}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
