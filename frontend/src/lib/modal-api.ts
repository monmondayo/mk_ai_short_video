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

export async function startRender(params: {
  job_id: string;
  num_stories?: number;
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

export async function getUploadUrl(params: {
  job_id: string;
  filename: string;
  content_type?: string;
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
