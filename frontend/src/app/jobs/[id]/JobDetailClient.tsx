"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import { startRender } from "@/lib/modal-api";
import TranscriptEditor from "@/components/TranscriptEditor";
import ProgressDisplay from "@/components/ProgressDisplay";
import VideoResults from "@/components/VideoResults";

type Job = {
  id: string;
  youtube_url: string;
  video_title: string;
  status: string;
  progress: Record<string, unknown>;
  num_stories: number;
  duration_preset: string;
  bg_color: string;
  add_captions: boolean;
  error_message: string;
  created_at: string;
};

type Transcript = {
  segments: Array<{ start: number; end: number; text: string }>;
} | null;

type OutputVideo = {
  id: string;
  rank: number;
  title: string;
  r2_url: string;
  duration_sec: number;
  file_size_mb: number;
};

export default function JobDetailClient({
  job,
  transcript,
  videos,
}: {
  job: Job;
  transcript: Transcript;
  videos: OutputVideo[];
}) {
  const router = useRouter();
  const supabase = createClient();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleStartRender = async () => {
    setSubmitting(true);
    setError("");
    try {
      // Save transcript first
      // Then trigger render
      const { call_id } = await startRender({
        job_id: job.id,
        num_stories: job.num_stories,
        duration_preset: job.duration_preset,
        bg_color: job.bg_color,
        add_captions: job.add_captions,
      });

      await supabase
        .from("jobs")
        .update({ modal_call_id: call_id, status: "extracting" })
        .eq("id", job.id);

      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start render");
    }
    setSubmitting(false);
  };

  const isProcessing = [
    "uploading",
    "downloading",
    "transcribing",
    "proofreading",
    "extracting",
    "rendering",
  ].includes(job.status);

  const showTranscript =
    job.status === "awaiting_review" && transcript?.segments;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <a
            href="/dashboard"
            className="text-sm text-gray-400 hover:text-gray-600"
          >
            Jobs
          </a>
          <span className="text-sm text-gray-300">/</span>
        </div>
        <h1 className="text-2xl font-bold text-gray-900">
          {job.video_title || "Untitled"}
        </h1>
        {job.youtube_url && job.youtube_url !== "(uploaded file)" && (
          <a
            href={job.youtube_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-blue-600 hover:underline"
          >
            {job.youtube_url}
          </a>
        )}
      </div>

      {/* Progress (shown when processing) */}
      {isProcessing && (
        <ProgressDisplay
          jobId={job.id}
          initialStatus={job.status}
          initialProgress={job.progress as Record<string, unknown> & { step?: string; current?: number; total?: number; detail?: string }}
        />
      )}

      {/* Error */}
      {job.status === "failed" && job.error_message && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <h3 className="font-medium text-red-800">Error</h3>
          <p className="text-sm text-red-600 mt-1">{job.error_message}</p>
        </div>
      )}

      {/* Transcript Editor (shown when awaiting review) */}
      {showTranscript && (
        <>
          <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
            <p className="text-sm text-purple-800">
              Transcription complete. Review and edit the transcript below,
              then click <strong>&quot;Save & Continue&quot;</strong> to start rendering.
            </p>
          </div>
          <TranscriptEditor
            jobId={job.id}
            segments={transcript!.segments}
            onSave={handleStartRender}
          />
          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
              {error}
            </p>
          )}
          {submitting && (
            <p className="text-sm text-blue-600">Starting render...</p>
          )}
        </>
      )}

      {/* Video Results (shown when complete) */}
      {job.status === "complete" && videos.length > 0 && (
        <VideoResults
          videos={videos}
          modalApiUrl={process.env.NEXT_PUBLIC_MODAL_API_URL || ""}
          jobId={job.id}
        />
      )}

      {/* Job Info */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-500 mb-2">
          Job Parameters
        </h3>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <dt className="text-gray-500">Stories</dt>
          <dd>{job.num_stories}</dd>
          <dt className="text-gray-500">Duration</dt>
          <dd>{job.duration_preset}s</dd>
          <dt className="text-gray-500">Background</dt>
          <dd>{job.bg_color}</dd>
          <dt className="text-gray-500">Captions</dt>
          <dd>{job.add_captions ? "Yes" : "No"}</dd>
          <dt className="text-gray-500">Created</dt>
          <dd>
            {new Date(job.created_at).toLocaleString("ja-JP")}
          </dd>
        </dl>
      </div>
    </div>
  );
}
