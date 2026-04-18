"use client";

import { useEffect, useState } from "react";
import { getDownloadUrl } from "@/lib/modal-api";

type OutputVideo = {
  id: string;
  rank: number;
  title: string;
  /**
   * Historical naming — this is actually an R2 object *key* relative to
   * the job's prefix (e.g. "output/02_video.mp4"), not a URL. We always
   * convert it to a presigned URL via `getDownloadUrl` before using it.
   */
  r2_url: string;
  duration_sec: number;
  file_size_mb: number;
};

type SignedUrls = { view_url: string; download_url: string };

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Sanitize a title into a safe filename. */
function buildFilename(rank: number, title: string): string {
  const safe = title
    .replace(/[\\/:*?"<>|]/g, "")
    .replace(/\s+/g, "_")
    .slice(0, 60);
  const padded = String(rank).padStart(2, "0");
  return `${padded}_${safe || "video"}.mp4`;
}

export default function VideoResults({
  videos,
  modalApiUrl,
  jobId,
}: {
  videos: OutputVideo[];
  modalApiUrl: string;
  jobId: string;
}) {
  // Cache of signed URLs per video id. Presigned URLs expire after 1h,
  // which is fine for a single page view.
  const [urls, setUrls] = useState<Record<string, SignedUrls>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const sorted = [...videos].sort((a, b) => a.rank - b.rank);

  // Fetch signed URLs for all videos on mount. We do this up-front so the
  // inline <video> preview can load, and so the Download button responds
  // instantly instead of triggering a round-trip on click.
  useEffect(() => {
    if (!modalApiUrl || videos.length === 0) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const entries = await Promise.all(
          videos.map(async (v) => {
            const signed = await getDownloadUrl({
              job_id: jobId,
              key: v.r2_url,
              filename: buildFilename(v.rank, v.title),
            });
            return [v.id, signed] as const;
          }),
        );
        if (cancelled) return;
        setUrls(Object.fromEntries(entries));
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "unknown error";
        setError(`Failed to load video URLs: ${msg}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // `videos` identity changes on every server refresh, but the meaningful
    // content (ids + keys) is stable — key the effect on ids instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, modalApiUrl, videos.map((v) => v.id).join(",")]);

  if (!videos || videos.length === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          Completed Videos ({videos.length})
        </h2>
        <span className="text-xs text-gray-400 font-mono">
          #{jobId.slice(0, 8)}
        </span>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
          {error}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {sorted.map((video) => {
          const filename = buildFilename(video.rank, video.title);
          const signed = urls[video.id];
          return (
            <div
              key={video.id}
              className="bg-white border border-gray-200 rounded-lg p-4 space-y-3"
            >
              <div className="min-w-0">
                <h3 className="font-medium text-gray-900 text-sm truncate">
                  #{video.rank} {video.title}
                </h3>
                <p className="text-xs text-gray-500 mt-1">
                  {formatDuration(video.duration_sec)} &middot;{" "}
                  {video.file_size_mb.toFixed(1)} MB
                </p>
              </div>

              {/* Inline preview — only render once we have a playable URL */}
              {signed ? (
                <video
                  src={signed.view_url}
                  controls
                  preload="metadata"
                  className="w-full rounded bg-black aspect-[9/16] object-contain"
                />
              ) : (
                <div className="w-full rounded bg-gray-100 aspect-[9/16] flex items-center justify-center text-xs text-gray-400">
                  {loading ? "Loading…" : "Preview unavailable"}
                </div>
              )}

              <div className="flex gap-2">
                <a
                  href={signed?.download_url ?? "#"}
                  download={filename}
                  onClick={(e) => {
                    if (!signed) e.preventDefault();
                  }}
                  aria-disabled={!signed}
                  className={`flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium ${
                    signed
                      ? "bg-blue-600 text-white hover:bg-blue-700"
                      : "bg-blue-300 text-white cursor-not-allowed"
                  }`}
                >
                  <svg
                    className="h-4 w-4"
                    viewBox="0 0 20 20"
                    fill="currentColor"
                  >
                    <path
                      fillRule="evenodd"
                      d="M10 3a1 1 0 011 1v7.586l2.293-2.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 111.414-1.414L9 11.586V4a1 1 0 011-1zM4 15a1 1 0 011-1h10a1 1 0 110 2H5a1 1 0 01-1-1z"
                      clipRule="evenodd"
                    />
                  </svg>
                  Download
                </a>
                <a
                  href={signed?.view_url ?? "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => {
                    if (!signed) e.preventDefault();
                  }}
                  aria-disabled={!signed}
                  className={`inline-flex items-center justify-center px-3 py-2 rounded-md border text-sm font-medium ${
                    signed
                      ? "border-gray-300 text-gray-700 hover:bg-gray-50"
                      : "border-gray-200 text-gray-400 cursor-not-allowed"
                  }`}
                  title="Open in new tab"
                >
                  Open
                </a>
              </div>

              <p
                className="text-xs text-gray-400 font-mono truncate"
                title={filename}
              >
                {filename}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
