"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase-browser";

export type StorySegment = {
  start: number;
  end: number;
  description?: string;
  transition_in?: string;
};

export type Story = {
  rank: number;
  title: string;
  theme?: string;
  hook: string;
  segments: StorySegment[];
};

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Editor for story-level text shown before rendering.
 *
 * Editable fields:
 * - ``title`` and ``hook``: rendered as overlay text on the short video.
 * - ``theme``: narrative-arc summary from Claude. Not burnt into the
 *   video but useful for the author's own notes / future re-generation.
 * - ``segments[].description``: per-segment summary. Also not rendered,
 *   but users frequently want to refine it because it captures "what
 *   this scene is about" in their own words.
 *
 * Segment timestamps stay read-only — editing them would drift from the
 * transcript and break the downstream ffmpeg cuts.
 *
 * Subtitles shown at the bottom of the final short come from the
 * transcript segments (edit those in TranscriptEditor). We considered
 * surfacing transcript text here but decided against it: a single
 * transcript segment can overlap multiple stories, so editing it in
 * the story context would silently affect other stories too.
 */
export default function StoryEditor({
  jobId,
  stories: initialStories,
  onSave,
  onSaveAndPrepareSubtitles,
}: {
  jobId: string;
  stories: Story[];
  /** Called after stories are persisted. May return a Promise to keep
   *  the button in its loading state until rendering has kicked off. */
  onSave?: () => void | Promise<void>;
  /** Called after stories are persisted when the user opted to review
   *  subtitles (prepare phase). If omitted, the checkbox is hidden. */
  onSaveAndPrepareSubtitles?: () => void | Promise<void>;
}) {
  const supabase = createClient();
  const [stories, setStories] = useState<Story[]>(initialStories);
  const [saving, setSaving] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [reviewSubtitles, setReviewSubtitles] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const updateStory = (index: number, patch: Partial<Story>) => {
    setStories((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
    setSaved(false);
  };

  const updateSegment = (
    storyIdx: number,
    segIdx: number,
    patch: Partial<StorySegment>,
  ) => {
    setStories((prev) => {
      const next = [...prev];
      const nextSegments = [...next[storyIdx].segments];
      nextSegments[segIdx] = { ...nextSegments[segIdx], ...patch };
      next[storyIdx] = { ...next[storyIdx], segments: nextSegments };
      return next;
    });
    setSaved(false);
  };

  const persist = async (): Promise<boolean> => {
    const { error } = await supabase
      .from("stories")
      .update({ stories_json: stories })
      .eq("job_id", jobId);
    if (error) {
      setErrorMsg(`Save failed: ${error.message}`);
      return false;
    }
    return true;
  };

  const handleSave = async () => {
    setSaving(true);
    setErrorMsg(null);
    const ok = await persist();
    if (ok) setSaved(true);
    setSaving(false);
  };

  const handleSaveAndRender = async () => {
    if (!onSave) return;
    setContinuing(true);
    setErrorMsg(null);
    try {
      const ok = await persist();
      if (!ok) {
        setContinuing(false);
        return;
      }
      setSaved(true);
      await onSave();
      // Parent router.refresh() will unmount this component shortly.
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Failed to start render");
      setContinuing(false);
    }
  };

  const handleSaveAndPrepareSubtitles = async () => {
    if (!onSaveAndPrepareSubtitles) return;
    setPreparing(true);
    setErrorMsg(null);
    try {
      const ok = await persist();
      if (!ok) {
        setPreparing(false);
        return;
      }
      setSaved(true);
      await onSaveAndPrepareSubtitles();
      // Parent router.refresh() will unmount this component shortly.
    } catch (e) {
      setErrorMsg(
        e instanceof Error ? e.message : "Failed to start subtitle prep",
      );
      setPreparing(false);
    }
  };

  const busy = saving || continuing || preparing;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Story Editor</h2>
        <div className="flex items-center gap-3">
          {saved && !continuing && (
            <span className="text-sm text-green-600">Saved</span>
          )}
          <button
            onClick={handleSave}
            disabled={busy}
            className="px-4 py-1.5 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? "Saving..." : "Save"}
          </button>
          {onSave && !reviewSubtitles && (
            <button
              onClick={handleSaveAndRender}
              disabled={busy}
              className="px-4 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {continuing && (
                <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
              {continuing ? "Starting render..." : "Save & Render"}
            </button>
          )}
          {onSaveAndPrepareSubtitles && reviewSubtitles && (
            <button
              onClick={handleSaveAndPrepareSubtitles}
              disabled={busy}
              className="px-4 py-1.5 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {preparing && (
                <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
              {preparing ? "Preparing subtitles..." : "Save & Prepare Subtitles"}
            </button>
          )}
        </div>
      </div>

      {onSaveAndPrepareSubtitles && (
        <label className="flex items-center gap-2 text-sm text-gray-700 bg-purple-50 border border-purple-200 rounded-lg px-3 py-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={reviewSubtitles}
            onChange={(e) => setReviewSubtitles(e.target.checked)}
            disabled={busy}
            className="rounded"
          />
          <span>
            <strong>字幕を編集してから描画</strong>
            （ローカル CLI の <code className="text-xs bg-white px-1 py-0.5 rounded border">--review-subtitles</code> と同等）
            — ストーリーをカット・結合・SRT 生成した後に一時停止し、字幕を編集してから最終レンダリングします。
          </span>
        </label>
      )}

      <p className="text-sm text-gray-500">
        <strong>タイトル</strong> はダッシュボード表示、<strong>フック</strong>{" "}
        は動画上部に大きく表示されます。<strong>テーマ</strong>{" "}
        と <strong>セグメント説明</strong>{" "}
        はメモ用（動画には描画されません）。タイムスタンプは編集不可です（動画カット位置と一致させるため）。
      </p>

      {errorMsg && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">
          {errorMsg}
        </div>
      )}

      <ul className="space-y-3">
        {stories.map((story, i) => {
          const totalSec = story.segments.reduce(
            (acc, s) => acc + (s.end - s.start),
            0,
          );
          return (
            <li
              key={story.rank}
              className="border border-gray-200 rounded-lg p-4 bg-white"
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-gray-100 text-gray-700 text-sm font-semibold shrink-0">
                  #{story.rank}
                </span>
                <div className="text-xs text-gray-500 shrink-0 text-right">
                  {story.segments.length} segs · {totalSec.toFixed(0)}s
                </div>
              </div>

              <label className="block text-xs font-medium text-gray-500 mb-1">
                タイトル / Title{" "}
                <span className="text-gray-400">(動画中央の文字)</span>
              </label>
              <input
                type="text"
                value={story.title}
                onChange={(e) => updateStory(i, { title: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm mb-3"
              />

              <label className="block text-xs font-medium text-gray-500 mb-1">
                フック / Hook{" "}
                <span className="text-gray-400">(動画上部の大きな文字)</span>
              </label>
              <textarea
                value={story.hook}
                onChange={(e) => updateStory(i, { hook: e.target.value })}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm resize-y mb-3"
              />

              <label className="block text-xs font-medium text-gray-500 mb-1">
                テーマ / Theme{" "}
                <span className="text-gray-400">(ストーリー要約・メモ用)</span>
              </label>
              <textarea
                value={story.theme ?? ""}
                onChange={(e) => updateStory(i, { theme: e.target.value })}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm resize-y"
                placeholder="ストーリー全体の要約や狙い"
              />

              <details className="mt-3" open>
                <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700 select-none">
                  セグメント本文 ({story.segments.length}件・編集可)
                </summary>
                <ul className="mt-2 space-y-2">
                  {story.segments.map((seg, j) => (
                    <li
                      key={j}
                      className="bg-gray-50 px-3 py-2 rounded border border-gray-200"
                    >
                      <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
                        <span className="font-mono shrink-0">
                          {formatTime(seg.start)}–{formatTime(seg.end)}
                        </span>
                        {seg.transition_in && (
                          <span className="text-blue-600 shrink-0">
                            [{seg.transition_in}]
                          </span>
                        )}
                        <span className="text-gray-300">
                          ・タイムスタンプは編集不可
                        </span>
                      </div>
                      <textarea
                        value={seg.description ?? ""}
                        onChange={(e) =>
                          updateSegment(i, j, { description: e.target.value })
                        }
                        rows={2}
                        className="w-full px-2 py-1 border border-gray-300 rounded bg-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm resize-y"
                        placeholder="このセグメントの説明"
                      />
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
