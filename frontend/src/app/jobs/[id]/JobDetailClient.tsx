"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import {
  startExtract,
  startPrepareSubtitles,
  startRender,
} from "@/lib/modal-api";
import TranscriptEditor from "@/components/TranscriptEditor";
import StoryEditor, { type Story } from "@/components/StoryEditor";
import SubtitleEditor, {
  type SubtitlesPayload,
} from "@/components/SubtitleEditor";
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
  stories,
  subtitles,
  videos,
}: {
  job: Job;
  transcript: Transcript;
  stories: Story[] | null;
  /** Per-story SRT blob from stories.subtitles_json — present only when
   *  the user opted into subtitle review (null otherwise). */
  subtitles: SubtitlesPayload | null;
  videos: OutputVideo[];
}) {
  const router = useRouter();
  const supabase = createClient();
  const [error, setError] = useState("");

  /** Phase 2a: after transcript review → kick off Claude extraction. */
  const handleStartExtract = async () => {
    setError("");
    try {
      const { call_id } = await startExtract({
        job_id: job.id,
        num_stories: job.num_stories,
        duration_preset: job.duration_preset,
      });

      await supabase
        .from("jobs")
        .update({ modal_call_id: call_id, status: "extracting" })
        .eq("id", job.id);

      router.refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to start extract";
      setError(msg);
      throw err; // let the editor re-enable its button
    }
  };

  /** Phase 2b: after story review → kick off ffmpeg rendering.
   *
   *  If ``subtitles_json`` exists on the stories row, render_videos_job
   *  on Modal detects it and only overlays (skipping cut+join). From
   *  the frontend's perspective we just call /start-render either way. */
  const handleStartRender = async () => {
    setError("");
    try {
      const { call_id } = await startRender({
        job_id: job.id,
        duration_preset: job.duration_preset,
        bg_color: job.bg_color,
        add_captions: job.add_captions,
      });

      await supabase
        .from("jobs")
        .update({ modal_call_id: call_id, status: "rendering" })
        .eq("id", job.id);

      router.refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to start render";
      setError(msg);
      throw err;
    }
  };

  /** Phase 2b-prep: after story review → cut + join + SRT (pause for
   *  subtitle review). Only triggered when the user checks
   *  「字幕を編集してから描画」in the StoryEditor. */
  const handleStartPrepareSubtitles = async () => {
    setError("");
    try {
      const { call_id } = await startPrepareSubtitles({
        job_id: job.id,
        duration_preset: job.duration_preset,
        bg_color: job.bg_color,
      });

      await supabase
        .from("jobs")
        .update({ modal_call_id: call_id, status: "preparing_subtitles" })
        .eq("id", job.id);

      router.refresh();
    } catch (err) {
      const msg = err instanceof Error
        ? err.message
        : "Failed to start subtitle preparation";
      setError(msg);
      throw err;
    }
  };

  const isProcessing = [
    "uploading",
    "downloading",
    "transcribing",
    "proofreading",
    "extracting",
    "preparing_subtitles",
    "rendering",
  ].includes(job.status);

  const showTranscript =
    job.status === "awaiting_review" && transcript?.segments;

  const showStories =
    job.status === "awaiting_story_review" && stories && stories.length > 0;

  const showSubtitles =
    job.status === "awaiting_subtitle_review" &&
    stories &&
    stories.length > 0 &&
    subtitles &&
    Object.keys(subtitles).length > 0;

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

      {/* Transcript Editor (shown when awaiting transcript review) */}
      {showTranscript && (
        <>
          <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
            <p className="text-sm text-purple-800">
              Transcription complete. Review and edit the transcript below,
              then click <strong>&quot;Save &amp; Continue&quot;</strong> to
              extract stories.
            </p>
          </div>
          <TranscriptEditor
            jobId={job.id}
            segments={transcript!.segments}
            onSave={handleStartExtract}
          />
          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
              {error}
            </p>
          )}
        </>
      )}

      {/* Story Editor (shown when awaiting story review) */}
      {showStories && (
        <>
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
            <p className="text-sm text-amber-800">
              Stories extracted. Review and edit titles and hooks below,
              then click <strong>&quot;Save &amp; Render&quot;</strong> to
              produce the short videos (or enable subtitle review to edit
              captions after cutting).
            </p>
          </div>
          <StoryEditor
            jobId={job.id}
            stories={stories!}
            onSave={handleStartRender}
            onSaveAndPrepareSubtitles={handleStartPrepareSubtitles}
          />
          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
              {error}
            </p>
          )}
        </>
      )}

      {/* Subtitle Editor (shown when awaiting subtitle review) */}
      {showSubtitles && (
        <>
          <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
            <p className="text-sm text-purple-800">
              Subtitles prepared. Review and edit each story&apos;s SRT
              below, then click <strong>&quot;Save &amp; Render&quot;</strong>
              {" "}to produce the final short videos.
            </p>
          </div>
          <SubtitleEditor
            jobId={job.id}
            stories={stories!.map((s) => ({
              rank: s.rank,
              title: s.title,
            }))}
            subtitles={subtitles!}
            onSave={handleStartRender}
          />
          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
              {error}
            </p>
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
