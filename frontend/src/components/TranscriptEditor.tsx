"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase-browser";

type Segment = {
  start: number;
  end: number;
  text: string;
};

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function TranscriptEditor({
  jobId,
  segments: initialSegments,
  onSave,
}: {
  jobId: string;
  segments: Segment[];
  /** Called after the transcript is persisted. Returning a Promise lets us
   *  keep the button disabled until the parent finishes its work. */
  onSave?: () => void | Promise<void>;
}) {
  const supabase = createClient();
  const [segments, setSegments] = useState(initialSegments);
  const [saving, setSaving] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const updateSegmentText = (index: number, text: string) => {
    const updated = [...segments];
    updated[index] = { ...updated[index], text };
    setSegments(updated);
    setSaved(false);
  };

  const persistTranscript = async (): Promise<boolean> => {
    const { error } = await supabase
      .from("transcripts")
      .update({ segments })
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
    const ok = await persistTranscript();
    if (ok) setSaved(true);
    setSaving(false);
  };

  const handleSaveAndContinue = async () => {
    if (!onSave) return;
    setContinuing(true);
    setErrorMsg(null);
    try {
      const ok = await persistTranscript();
      if (!ok) {
        setContinuing(false);
        return;
      }
      setSaved(true);
      await onSave();
      // Keep `continuing` true — parent is about to router.refresh() and
      // this component will unmount when status leaves awaiting_review.
    } catch (e) {
      setErrorMsg(
        e instanceof Error ? e.message : "Failed to start render",
      );
      setContinuing(false);
    }
  };

  const busy = saving || continuing;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          Transcript Editor
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
              onClick={handleSaveAndContinue}
              disabled={busy}
              className="px-4 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {continuing && (
                <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
              {continuing ? "Starting render..." : "Save & Continue"}
            </button>
          )}
        </div>
      </div>

      {errorMsg && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">
          {errorMsg}
        </div>
      )}

      <p className="text-sm text-gray-500">
        Edit the transcript below. Each row is a timed segment. Fix any
        transcription errors before proceeding.
      </p>

      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-3 py-2 text-left text-gray-600 w-24">Time</th>
              <th className="px-3 py-2 text-left text-gray-600">Text</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {segments.map((seg, i) => (
              <tr key={i} className="hover:bg-gray-50">
                <td className="px-3 py-1.5 text-gray-400 font-mono text-xs whitespace-nowrap align-top pt-2.5">
                  {formatTime(seg.start)}
                </td>
                <td className="px-3 py-1.5">
                  <textarea
                    value={seg.text}
                    onChange={(e) => updateSegmentText(i, e.target.value)}
                    rows={1}
                    className="w-full px-2 py-1 border border-transparent rounded hover:border-gray-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none resize-none"
                    onInput={(e) => {
                      const el = e.target as HTMLTextAreaElement;
                      el.style.height = "auto";
                      el.style.height = el.scrollHeight + "px";
                    }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
