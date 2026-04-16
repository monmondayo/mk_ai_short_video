"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase-browser";

type JobProgress = {
  step?: string;
  current?: number;
  total?: number;
  detail?: string;
};

const STEP_LABELS: Record<string, string> = {
  downloading: "Downloading from storage",
  transcribing: "Transcribing with Whisper",
  proofreading: "Proofreading with AI",
  awaiting_review: "Waiting for transcript review",
  extracting: "Extracting stories with AI",
  rendering: "Rendering short videos",
  complete: "Complete!",
  failed: "Failed",
};

export default function ProgressDisplay({
  jobId,
  initialStatus,
  initialProgress,
}: {
  jobId: string;
  initialStatus: string;
  initialProgress: JobProgress;
}) {
  const supabase = createClient();
  const [status, setStatus] = useState(initialStatus);
  const [progress, setProgress] = useState<JobProgress>(initialProgress || {});

  useEffect(() => {
    // Subscribe to Realtime changes on this job
    const channel = supabase
      .channel(`job-${jobId}`)
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "jobs",
          filter: `id=eq.${jobId}`,
        },
        (payload) => {
          const updated = payload.new as Record<string, unknown>;
          if (updated.status) setStatus(updated.status as string);
          if (updated.progress) setProgress(updated.progress as JobProgress);
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [jobId, supabase]);

  const stepLabel = STEP_LABELS[status] || status;
  const pct =
    progress.total && progress.total > 0
      ? Math.round(((progress.current || 0) / progress.total) * 100)
      : 0;
  const isRunning = !["complete", "failed", "awaiting_review", "pending"].includes(status);

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <div className="flex items-center gap-3 mb-3">
        {isRunning && (
          <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
        )}
        <h3 className="font-semibold text-gray-900">{stepLabel}</h3>
      </div>

      {progress.total && progress.total > 0 && (
        <div className="mb-2">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>
              {progress.current} / {progress.total}
            </span>
            <span>{pct}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {progress.detail && (
        <p className="text-sm text-gray-500">{progress.detail}</p>
      )}

      {status === "failed" && (
        <p className="text-sm text-red-600 mt-2">
          An error occurred. Please check the job details.
        </p>
      )}
    </div>
  );
}
