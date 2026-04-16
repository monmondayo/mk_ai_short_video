"use client";

type OutputVideo = {
  id: string;
  rank: number;
  title: string;
  r2_url: string;
  duration_sec: number;
  file_size_mb: number;
};

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
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
  if (!videos || videos.length === 0) return null;

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold text-gray-900">
        Completed Videos ({videos.length})
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {videos
          .sort((a, b) => a.rank - b.rank)
          .map((video) => (
            <div
              key={video.id}
              className="bg-white border border-gray-200 rounded-lg p-4"
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <h3 className="font-medium text-gray-900 text-sm truncate">
                    #{video.rank} {video.title}
                  </h3>
                  <p className="text-xs text-gray-500 mt-1">
                    {formatDuration(video.duration_sec)} &middot;{" "}
                    {video.file_size_mb.toFixed(1)} MB
                  </p>
                </div>
              </div>
              <p className="text-xs text-gray-400 mt-2 truncate font-mono">
                {video.r2_url}
              </p>
            </div>
          ))}
      </div>
    </div>
  );
}
