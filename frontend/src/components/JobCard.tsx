"use client";

import Link from "next/link";

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
  extracting: { label: "Extracting", color: "bg-orange-100 text-orange-700" },
  rendering: { label: "Rendering", color: "bg-orange-100 text-orange-700" },
  complete: { label: "Complete", color: "bg-green-100 text-green-700" },
  failed: { label: "Failed", color: "bg-red-100 text-red-700" },
};

export default function JobCard({ job }: { job: Job }) {
  const status = STATUS_LABELS[job.status] || STATUS_LABELS.pending;
  const date = new Date(job.created_at).toLocaleDateString("ja-JP", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <Link
      href={`/jobs/${job.id}`}
      className="block bg-white rounded-lg border border-gray-200 p-4 hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="font-medium text-gray-900 truncate">
            {job.video_title || job.youtube_url}
          </h3>
          <p className="text-sm text-gray-500 mt-1 truncate">{job.youtube_url}</p>
        </div>
        <span
          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${status.color}`}
        >
          {status.label}
        </span>
      </div>
      <p className="text-xs text-gray-400 mt-2">{date}</p>
    </Link>
  );
}
