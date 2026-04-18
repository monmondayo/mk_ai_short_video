"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { createClient } from "@/lib/supabase-browser";
import { deleteR2Files } from "@/lib/modal-api";

type Job = {
  id: string;
  youtube_url: string;
  video_title: string;
  status: string;
  progress: Record<string, unknown>;
  created_at: string;
};

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending: { label: "Pending", color: "bg-gray-100 text-gray-700" },
  uploading: { label: "Uploading", color: "bg-blue-100 text-blue-700" },
  downloading: { label: "Downloading", color: "bg-blue-100 text-blue-700" },
  transcribing: { label: "Transcribing", color: "bg-yellow-100 text-yellow-700" },
  proofreading: { label: "Proofreading", color: "bg-yellow-100 text-yellow-700" },
  awaiting_review: { label: "Review Ready", color: "bg-purple-100 text-purple-700" },
  awaiting_story_review: { label: "Stories Ready", color: "bg-purple-100 text-purple-700" },
  extracting: { label: "Extracting", color: "bg-orange-100 text-orange-700" },
  rendering: { label: "Rendering", color: "bg-orange-100 text-orange-700" },
  complete: { label: "Complete", color: "bg-green-100 text-green-700" },
  failed: { label: "Failed", color: "bg-red-100 text-red-700" },
};

export default function JobCard({ job }: { job: Job }) {
  const router = useRouter();
  const supabase = createClient();
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  const status = STATUS_LABELS[job.status] || STATUS_LABELS.pending;
  const date = new Date(job.created_at).toLocaleDateString("ja-JP", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  const handleDelete = async (e: React.MouseEvent) => {
    // The card is not a <Link> anymore but we still stop propagation in
    // case the button ever ends up nested under a clickable parent.
    e.preventDefault();
    e.stopPropagation();

    const label = job.video_title || job.youtube_url || "this job";
    if (!window.confirm(`Delete "${label}"? This cannot be undone.`)) return;

    setError("");
    setDeleting(true);
    try {
      // 1. Delete DB row first. RLS enforces that only the owner can
      //    delete, and ON DELETE CASCADE removes transcripts/stories/
      //    output_videos rows tied to this job.
      const { error: dbError } = await supabase
        .from("jobs")
        .delete()
        .eq("id", job.id);
      if (dbError) throw new Error(dbError.message);

      // 2. Clean up R2 best-effort. A failure here leaves orphaned
      //    objects but doesn't block the user — the job is already
      //    gone from their dashboard.
      try {
        await deleteR2Files(job.id);
      } catch (r2err) {
        console.warn(`R2 cleanup failed for job ${job.id}:`, r2err);
      }

      router.refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setError(msg);
      setDeleting(false);
    }
  };

  return (
    <div className="relative bg-white rounded-lg border border-gray-200 hover:shadow-md transition-shadow">
      <Link
        href={`/jobs/${job.id}`}
        className="block p-4 pr-14"
        aria-disabled={deleting}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="font-medium text-gray-900 truncate">
              {job.video_title || job.youtube_url}
            </h3>
            <p className="text-sm text-gray-500 mt-1 truncate">
              {job.youtube_url}
            </p>
          </div>
          <span
            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${status.color}`}
          >
            {status.label}
          </span>
        </div>
        <p className="text-xs text-gray-400 mt-2">{date}</p>
        {error && (
          <p className="text-xs text-red-600 mt-2">Delete failed: {error}</p>
        )}
      </Link>

      {/* Delete button — positioned absolute so it lives outside the <Link>
          click area. Kept small to avoid visual clutter on the list. */}
      <button
        type="button"
        onClick={handleDelete}
        disabled={deleting}
        aria-label="Delete job"
        title="Delete job"
        className="absolute top-3 right-3 p-1.5 rounded-md text-gray-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {deleting ? (
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
              className="opacity-25"
            />
            <path
              d="M4 12a8 8 0 018-8"
              stroke="currentColor"
              strokeWidth="4"
              className="opacity-75"
            />
          </svg>
        ) : (
          <svg
            className="h-4 w-4"
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              fillRule="evenodd"
              d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2h.31l.771 9.252A2 2 0 007.074 17h5.852a2 2 0 001.993-1.748L15.69 6H16a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zm1 6a1 1 0 011 1v6a1 1 0 11-2 0V9a1 1 0 011-1zm-3 1a1 1 0 012 0v6a1 1 0 11-2 0V9zm6 0a1 1 0 10-2 0v6a1 1 0 102 0V9z"
              clipRule="evenodd"
            />
          </svg>
        )}
      </button>
    </div>
  );
}
