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
  onSave?: () => void;
}) {
  const supabase = createClient();
  const [segments, setSegments] = useState(initialSegments);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const updateSegmentText = (index: number, text: string) => {
    const updated = [...segments];
    updated[index] = { ...updated[index], text };
    setSegments(updated);
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    const { error } = await supabase
      .from("transcripts")
      .update({ segments })
      .eq("job_id", jobId);

    if (error) {
      alert(`Save failed: ${error.message}`);
    } else {
      setSaved(true);
    }
    setSaving(false);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          Transcript Editor
        </h2>
        <div className="flex items-center gap-3">
          {saved && (
            <span className="text-sm text-green-600">Saved</span>
          )}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
          {onSave && (
            <button
              onClick={onSave}
              className="px-4 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
            >
              Save & Continue
            </button>
          )}
        </div>
      </div>

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
