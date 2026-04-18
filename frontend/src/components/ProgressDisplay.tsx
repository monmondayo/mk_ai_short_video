"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
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
  awaiting_story_review: "Waiting for story review",
  preparing_subtitles: "Cutting segments & generating subtitles",
  awaiting_subtitle_review: "Waiting for subtitle review",
  rendering: "Rendering short videos",
  complete: "Complete!",
  failed: "Failed",
};

// Status values that require fetching server-side data we don't have on
// the client yet (transcript, stories, subtitles, output videos). When we
// transition into one of these, refresh the server component to pull
// them in.
const REFRESH_ON = new Set([
  "awaiting_review",
  "awaiting_story_review",
  "awaiting_subtitle_review",
  "complete",
  "failed",
]);

// Terminal states — no point polling after we hit one.
const DONE_STATES = new Set(["complete", "failed"]);

const POLL_INTERVAL_MS = 4000;

export default function ProgressDisplay({
  jobId,
  initialStatus,
  initialProgress,
}: {
  jobId: string;
  initialStatus: string;
  initialProgress: JobProgress;
}) {
  const router = useRouter();
  const supabase = createClient();
  const [status, setStatus] = useState(initialStatus);
  const [progress, setProgress] = useState<JobProgress>(initialProgress || {});

  // Keep refs so the interval closure always sees the latest values
  // without having to re-create the interval on every state change.
  const statusRef = useRef(status);
  statusRef.current = status;

  // Apply a status transition, refreshing the server component when we
  // cross into a state that requires fresh server data.
  const applyStatus = (nextStatus: string) => {
    setStatus((prev) => {
      if (nextStatus !== prev && REFRESH_ON.has(nextStatus)) {
        router.refresh();
      }
      return nextStatus;
    });
  };

  // Realtime subscription — primary channel for live updates.
  useEffect(() => {
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
          const nextStatus = updated.status as string | undefined;
          if (nextStatus) applyStatus(nextStatus);
          if (updated.progress) setProgress(updated.progress as JobProgress);
        },
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
    // applyStatus/router/supabase are stable for this component's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Polling fallback — Supabase Realtime occasionally fails to deliver
  // postgres_changes events (WebSocket drop, RLS race, replication lag).
  // Without this fallback the UI can sit on "Transcribing..." indefinitely
  // while the backend has already moved on. Polls every few seconds until
  // we hit a terminal state, then stops.
  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      if (cancelled) return;
      if (DONE_STATES.has(statusRef.current)) return;
      const { data, error } = await supabase
        .from("jobs")
        .select("status, progress")
        .eq("id", jobId)
        .single();
      if (cancelled || error || !data) return;
      const nextStatus = data.status as string;
      if (nextStatus && nextStatus !== statusRef.current) {
        applyStatus(nextStatus);
      }
      if (data.progress) {
        setProgress(data.progress as JobProgress);
      }
    };

    const id = setInterval(tick, POLL_INTERVAL_MS);
    // Kick one immediately so late-mounted components catch up fast.
    tick();

    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const stepLabel = STEP_LABELS[status] || status;
  const pct =
    progress.total && progress.total > 0
      ? Math.round(((progress.current || 0) / progress.total) * 100)
      : 0;
  const isRunning = ![
    "complete",
    "failed",
    "awaiting_review",
    "awaiting_story_review",
    "awaiting_subtitle_review",
    "pending",
  ].includes(status);

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
