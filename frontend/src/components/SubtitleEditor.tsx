"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase-browser";
import { getDownloadUrl } from "@/lib/modal-api";

/**
 * SRT payload saved by ``prepare_subtitles_job`` into
 * ``stories.subtitles_json``. Keyed by story rank (as a string — JSONB
 * doesn't preserve integer keys).
 */
export type SubtitlesPayload = Record<
  string,
  {
    srt: string;
    durations: number[];
    joined_key: string;
  }
>;

/**
 * Story summary passed alongside so we can show rank + title headers
 * above each SRT textarea. We only need a subset of the full Story
 * type here.
 */
export type SubtitleStoryMeta = {
  rank: number;
  title: string;
};

/**
 * Editor for the per-story SRT captions produced by the prepare phase.
 *
 * Cloud equivalent of the local CLI's ``--review-subtitles`` pause:
 * Modal has already cut + joined the segments and generated a first-pass
 * SRT for each story; the user now reviews and optionally edits the
 * caption text before the final overlay render.
 *
 * We edit the SRT as raw text (rather than parsing into cue objects):
 * - Cue timestamps are anchored to the already-joined intermediate
 *   video, so normal edits stay consistent with the durations stored
 *   alongside.
 * - The CLI flow edits SRT files directly in ``$EDITOR``; mirroring
 *   that keeps the two flows equivalent and lets power-users paste an
 *   externally-edited SRT back in.
 *
 * The ``joined_key`` and ``durations`` fields are passed through
 * untouched — only ``srt`` is user-editable.
 */
export default function SubtitleEditor({
  jobId,
  stories,
  subtitles: initialSubtitles,
  onSave,
}: {
  jobId: string;
  stories: SubtitleStoryMeta[];
  subtitles: SubtitlesPayload;
  /** Called after subtitles are persisted. May return a Promise so the
   *  button can stay in loading state until the render kickoff settles. */
  onSave?: () => void | Promise<void>;
}) {
  const supabase = createClient();
  const [subtitles, setSubtitles] = useState<SubtitlesPayload>(initialSubtitles);
  const [saving, setSaving] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const entries = Object.entries(initialSubtitles).filter(
      ([, entry]) => entry.joined_key,
    );
    if (!entries.length) return;

    Promise.all(
      entries.map(async ([rank, entry]) => {
        const { view_url } = await getDownloadUrl({
          job_id: jobId,
          key: entry.joined_key,
          filename: `story-${rank}-joined.mp4`,
        });
        return [rank, view_url] as const;
      }),
    )
      .then((pairs) => {
        if (!cancelled) setPreviewUrls(Object.fromEntries(pairs));
      })
      .catch((e) => {
        if (!cancelled) {
          setPreviewError(
            e instanceof Error ? e.message : "Failed to load preview videos",
          );
        }
      })

    return () => {
      cancelled = true;
    };
  }, [jobId, initialSubtitles]);

  const updateSrt = (rank: number, srt: string) => {
    setSubtitles((prev) => {
      const key = String(rank);
      const existing = prev[key];
      if (!existing) return prev;
      return { ...prev, [key]: { ...existing, srt } };
    });
    setSaved(false);
  };

  const persist = async (): Promise<boolean> => {
    // .select() returns the updated rows — if RLS blocks the update it
    // yields an empty array instead of an error, so we check length to
    // catch silent failures. Without this, a missing UPDATE policy would
    // leave the UI showing "Saved" while the DB stayed unchanged.
    const { data, error } = await supabase
      .from("stories")
      .update({ subtitles_json: subtitles })
      .eq("job_id", jobId)
      .select("id");
    if (error) {
      setErrorMsg(`Save failed: ${error.message}`);
      return false;
    }
    if (!data || data.length === 0) {
      setErrorMsg(
        "Save failed: no rows updated (check Supabase RLS UPDATE policy on stories)",
      );
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
      // Parent will router.refresh() and unmount this shortly.
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Failed to start render");
      setContinuing(false);
    }
  };

  const busy = saving || continuing;

  // Sort stories by rank for consistent ordering with StoryEditor.
  const ordered = [...stories].sort((a, b) => a.rank - b.rank);

  // Count how many SRT cues each story has — useful for the user to
  // see that an edit actually parses (one empty line between cues).
  const countCues = (srt: string): number => {
    if (!srt.trim()) return 0;
    return srt.trim().split(/\n\s*\n/).filter(Boolean).length;
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          Subtitle Editor
        </h2>
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
          {onSave && (
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
        </div>
      </div>

      <p className="text-sm text-gray-500">
        ストーリーごとの字幕 (SRT) を確認・編集してください。タイムスタンプは結合済み動画に対する相対時間です。<br />
        編集が終わったら <strong>Save &amp; Render</strong> で最終レンダリングを開始します。
      </p>

      {errorMsg && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">
          {errorMsg}
        </div>
      )}

      {previewError && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm text-amber-800">
          Preview videos could not be loaded: {previewError}
        </div>
      )}

      <ul className="space-y-3">
        {ordered.map((story) => {
          const key = String(story.rank);
          const entry = subtitles[key];
          if (!entry) {
            // Prepare phase skipped this story (ffmpeg error etc.).
            return (
              <li
                key={story.rank}
                className="border border-amber-200 bg-amber-50 rounded-lg p-4 text-sm"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-amber-100 text-amber-800 text-sm font-semibold shrink-0">
                    #{story.rank}
                  </span>
                  <span className="font-medium text-amber-900">
                    {story.title || "(untitled)"}
                  </span>
                </div>
                <p className="text-amber-800">
                  このストーリーは準備フェーズで失敗したため、字幕編集をスキップします。他のストーリーの編集は続行できます。
                </p>
              </li>
            );
          }
          const cueCount = countCues(entry.srt);
          return (
            <li
              key={story.rank}
              className="border border-gray-200 rounded-lg p-4 bg-white"
            >
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-gray-100 text-gray-700 text-sm font-semibold shrink-0">
                    #{story.rank}
                  </span>
                  <span className="font-medium text-gray-900 truncate">
                    {story.title || "(untitled)"}
                  </span>
                </div>
                <div className="text-xs text-gray-500 shrink-0">
                  {cueCount} cues · {entry.durations.length} segs
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-[minmax(240px,360px)_minmax(0,1fr)]">
                <div className="space-y-2">
                  <div className="aspect-[9/16] max-h-[560px] overflow-hidden rounded-lg bg-gray-950">
                    {previewUrls[key] ? (
                      <video
                        src={previewUrls[key]}
                        controls
                        preload="metadata"
                        className="h-full w-full object-contain"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-gray-300">
                        {previewError ? "Preview unavailable" : "Loading preview..."}
                      </div>
                    )}
                  </div>
                  <p className="text-xs text-gray-500">
                    字幕合成前の切り出し済みショート動画
                  </p>
                </div>

                <textarea
                  value={entry.srt}
                  onChange={(e) => updateSrt(story.rank, e.target.value)}
                  rows={Math.min(24, Math.max(12, entry.srt.split("\n").length))}
                  className="min-h-[360px] w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-xs font-mono resize-y whitespace-pre"
                  spellCheck={false}
                  placeholder="1&#10;00:00:00,000 --> 00:00:02,500&#10;字幕テキスト"
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
