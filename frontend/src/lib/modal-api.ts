const MODAL_API_URL = process.env.NEXT_PUBLIC_MODAL_API_URL || "";

export async function submitJob(params: {
  job_id: string;
  whisper_model?: string;
  whisper_language?: string;
  skip_proofread?: boolean;
}) {
  const res = await fetch(`${MODAL_API_URL}/submit-job`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`submit-job failed: ${res.statusText}`);
  return res.json();
}

export async function startExtract(params: {
  job_id: string;
  num_stories?: number;
  duration_preset?: string;
}) {
  const res = await fetch(`${MODAL_API_URL}/start-extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`start-extract failed: ${res.statusText}`);
  return res.json();
}

export async function startRender(params: {
  job_id: string;
  duration_preset?: string;
  bg_color?: string;
  add_captions?: boolean;
}) {
  const res = await fetch(`${MODAL_API_URL}/start-render`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`start-render failed: ${res.statusText}`);
  return res.json();
}

/**
 * Phase 2b-prep: kicks off subtitle preparation (cut + join + SRT
 * generation). Mirrors the local CLI's ``--review-subtitles`` pause.
 *
 * After Modal finishes, the job flips to ``awaiting_subtitle_review``
 * and ``stories.subtitles_json`` contains one entry per story with
 * ``{srt, durations, joined_key}``. The subsequent ``startRender`` call
 * will detect that column and skip straight to Phase 2 (overlay only).
 */
export async function startPrepareSubtitles(params: {
  job_id: string;
  duration_preset?: string;
  bg_color?: string;
}) {
  const res = await fetch(`${MODAL_API_URL}/start-prepare-subtitles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    throw new Error(`start-prepare-subtitles failed: ${res.statusText}`);
  }
  return res.json();
}

export async function getUploadUrl(params: {
  job_id: string;
  filename: string;
  content_type?: string;
  /** "source" for the main video (default) or "input" for overlay images. */
  path_prefix?: "source" | "input";
}) {
  const res = await fetch(`${MODAL_API_URL}/upload-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`upload-url failed: ${res.statusText}`);
  return res.json();
}

export async function getJobStatus(callId: string) {
  const res = await fetch(`${MODAL_API_URL}/job-status/${callId}`);
  if (!res.ok) throw new Error(`job-status failed: ${res.statusText}`);
  return res.json();
}

/**
 * Get presigned URLs for viewing / downloading a rendered output video.
 *
 * The `key` argument is the value of `output_videos.r2_url` — an R2 object
 * key relative to the job prefix (e.g. "output/02_video.mp4"), NOT a URL.
 * Both returned URLs are presigned and expire after 1 hour.
 */
export async function getDownloadUrl(params: {
  job_id: string;
  key: string;
  filename: string;
}): Promise<{ view_url: string; download_url: string }> {
  const res = await fetch(`${MODAL_API_URL}/download-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`download-url failed: ${res.statusText}`);
  return res.json();
}

/**
 * Delete every R2 object under `jobs/<job_id>/`. Best-effort — callers
 * should catch errors so DB deletion still succeeds if R2 cleanup fails
 * (orphaned objects can be cleaned up later).
 */
export async function deleteR2Files(jobId: string): Promise<{ deleted: number }> {
  const res = await fetch(`${MODAL_API_URL}/delete-r2-files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId }),
  });
  if (!res.ok) throw new Error(`delete-r2-files failed: ${res.statusText}`);
  return res.json();
}
