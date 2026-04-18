"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import { getUploadUrl, submitJob } from "@/lib/modal-api";
import Navbar from "@/components/Navbar";

const DURATION_OPTIONS = ["0-15", "15-30", "30-45", "30-60", "45-60", "60-90"];

export default function NewJobPage() {
  const router = useRouter();
  const supabase = createClient();

  const [url, setUrl] = useState("");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);

  // Accept only image files, merge with existing selection, dedupe by name+size
  const IMAGE_EXTS = ["jpg", "jpeg", "png", "webp", "bmp"];
  const addImages = (incoming: FileList | File[]) => {
    const next = Array.from(incoming).filter((f) => {
      if (f.type.startsWith("image/")) return true;
      const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
      return IMAGE_EXTS.includes(ext);
    });
    setImageFiles((prev) => {
      const map = new Map<string, File>();
      for (const f of [...prev, ...next]) {
        map.set(`${f.name}:${f.size}`, f);
      }
      return Array.from(map.values());
    });
  };
  const removeImage = (index: number) => {
    setImageFiles((prev) => prev.filter((_, i) => i !== index));
  };
  const [numStories, setNumStories] = useState(10);
  const [duration, setDuration] = useState("30-60");
  const [whisperModel, setWhisperModel] = useState("medium");
  const [whisperLang, setWhisperLang] = useState("ja");
  const [bgColor, setBgColor] = useState("white");
  const [addCaptions, setAddCaptions] = useState(true);
  const [skipProofread, setSkipProofread] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!videoFile) {
      setError("Please select a video file to upload.");
      return;
    }
    setLoading(true);
    setError("");

    // Track the job ID so we can mark it failed if any post-insert step
    // throws. Without this, a network hiccup during upload leaves the
    // job stuck at "uploading" forever.
    let createdJobId: string | null = null;

    try {
      // 1. Get current user
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) throw new Error("Not authenticated");

      // 2. Create job in Supabase.
      // Strip the file extension from the title so it isn't rendered as
      // "video.mp4" on the shorts. We only strip common video extensions
      // to avoid mangling titles that legitimately contain dots.
      const titleFromFile = videoFile.name.replace(
        /\.(mp4|mkv|mov|webm|avi|m4v|flv|wmv)$/i,
        "",
      );
      setStatus("Creating job...");
      const { data: job, error: jobError } = await supabase
        .from("jobs")
        .insert({
          user_id: user.id,
          youtube_url: url || "(uploaded file)",
          video_title: titleFromFile,
          whisper_model: whisperModel,
          whisper_language: whisperLang,
          num_stories: numStories,
          duration_preset: duration,
          bg_color: bgColor,
          add_captions: addCaptions,
          skip_proofread: skipProofread,
          status: "uploading",
        })
        .select()
        .single();

      if (jobError) throw new Error(jobError.message);
      createdJobId = job.id;

      // 3. Get presigned upload URL from Modal
      setStatus("Getting upload URL...");
      const { upload_url } = await getUploadUrl({
        job_id: job.id,
        filename: videoFile.name,
        content_type: videoFile.type || "video/mp4",
      });

      // 4. Upload video directly to R2
      setStatus(`Uploading ${(videoFile.size / 1024 / 1024).toFixed(0)} MB...`);
      const uploadRes = await fetch(upload_url, {
        method: "PUT",
        headers: { "Content-Type": videoFile.type || "video/mp4" },
        body: videoFile,
      });
      if (!uploadRes.ok) throw new Error("Video upload failed");

      // 4b. Upload overlay images (optional)
      for (let i = 0; i < imageFiles.length; i++) {
        const img = imageFiles[i];
        setStatus(`Uploading image ${i + 1}/${imageFiles.length}: ${img.name}`);
        const { upload_url: imgUrl } = await getUploadUrl({
          job_id: job.id,
          filename: img.name,
          content_type: img.type || "image/jpeg",
          path_prefix: "input",
        });
        const imgRes = await fetch(imgUrl, {
          method: "PUT",
          headers: { "Content-Type": img.type || "image/jpeg" },
          body: img,
        });
        if (!imgRes.ok) throw new Error(`Image upload failed: ${img.name}`);
      }

      // 5. Submit transcription job to Modal
      setStatus("Starting transcription...");
      const { call_id } = await submitJob({
        job_id: job.id,
        whisper_model: whisperModel,
        whisper_language: whisperLang,
        skip_proofread: skipProofread,
      });

      // 6. Save call_id and update status
      await supabase
        .from("jobs")
        .update({ modal_call_id: call_id, status: "transcribing" })
        .eq("id", job.id);

      // Navigate to job detail page
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setError(msg);
      setLoading(false);

      // If the row was already inserted, flag it as failed so it doesn't
      // sit at "uploading" forever on the dashboard. Best-effort — we
      // swallow errors here so the original failure surfaces to the user.
      if (createdJobId) {
        try {
          await supabase
            .from("jobs")
            .update({ status: "failed", error_message: msg })
            .eq("id", createdJobId);
        } catch (markErr) {
          console.warn("Failed to mark job as failed:", markErr);
        }
      }
    }
  };

  return (
    <>
      <Navbar />
      <main className="max-w-2xl mx-auto w-full px-4 py-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">New Job</h1>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* YouTube URL (optional - for reference) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              YouTube URL (optional - for reference)
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              placeholder="https://youtu.be/..."
            />
            <p className="text-xs text-gray-500 mt-1">
              Download the video locally, then upload below.
              Modal cannot download from YouTube directly.
            </p>
          </div>

          {/* Video Upload */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Video File *
            </label>
            <input
              type="file"
              accept="video/*"
              onChange={(e) => setVideoFile(e.target.files?.[0] || null)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
              required
            />
            {videoFile && (
              <p className="text-xs text-gray-500 mt-1">
                {videoFile.name} ({(videoFile.size / 1024 / 1024).toFixed(1)} MB)
              </p>
            )}
          </div>

          {/* Overlay Images (optional) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Overlay Images (optional)
            </label>
            <div
              onClick={() => imageInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  imageInputRef.current?.click();
                }
              }}
              role="button"
              tabIndex={0}
              onDragEnter={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(true);
              }}
              onDragOver={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!isDragging) setIsDragging(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                e.stopPropagation();
                // Only hide when leaving the dropzone itself, not a child
                if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                setIsDragging(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(false);
                if (e.dataTransfer.files?.length) {
                  addImages(e.dataTransfer.files);
                }
              }}
              className={`w-full px-4 py-8 border-2 border-dashed rounded-lg text-center cursor-pointer transition ${
                isDragging
                  ? "border-blue-500 bg-blue-50"
                  : "border-gray-300 bg-gray-50 hover:bg-gray-100"
              }`}
            >
              <p className="text-sm text-gray-700 font-medium">
                Drop images here or click to select
              </p>
              <p className="text-xs text-gray-500 mt-1">
                JPG, PNG, WebP, BMP — multiple allowed
              </p>
              <input
                ref={imageInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/bmp"
                multiple
                onChange={(e) => {
                  if (e.target.files) addImages(e.target.files);
                  // Reset so selecting the same file again still fires onChange
                  e.target.value = "";
                }}
                className="hidden"
              />
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Static images composited around the subtitles (equivalent to the
              local <code>input/</code> folder). Provide at least 4 for the best
              layout; fewer will be repeated.
            </p>
            {imageFiles.length > 0 && (
              <ul className="mt-2 space-y-1">
                {imageFiles.map((f, i) => (
                  <li
                    key={`${f.name}:${f.size}`}
                    className="flex items-center justify-between text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded"
                  >
                    <span className="truncate">
                      {f.name}{" "}
                      <span className="text-gray-400">
                        ({(f.size / 1024).toFixed(0)} KB)
                      </span>
                    </span>
                    <button
                      type="button"
                      onClick={() => removeImage(i)}
                      className="ml-2 text-red-500 hover:text-red-700 px-2 shrink-0"
                      aria-label={`Remove ${f.name}`}
                    >
                      ×
                    </button>
                  </li>
                ))}
                <li className="text-xs text-gray-500 pt-1">
                  {imageFiles.length} file{imageFiles.length === 1 ? "" : "s"} selected
                </li>
              </ul>
            )}
          </div>

          {/* Parameters grid */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Number of shorts
              </label>
              <input
                type="number"
                min={1}
                max={30}
                value={numStories}
                onChange={(e) => setNumStories(Number(e.target.value))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Duration (sec)
              </label>
              <select
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              >
                {DURATION_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    {d}s
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Whisper model
              </label>
              <select
                value={whisperModel}
                onChange={(e) => setWhisperModel(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              >
                <option value="tiny">tiny (fastest)</option>
                <option value="base">base</option>
                <option value="small">small</option>
                <option value="medium">medium (recommended)</option>
                <option value="large">large (best quality)</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Language
              </label>
              <select
                value={whisperLang}
                onChange={(e) => setWhisperLang(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              >
                <option value="ja">Japanese</option>
                <option value="en">English</option>
                <option value="">Auto-detect</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Background
              </label>
              <select
                value={bgColor}
                onChange={(e) => setBgColor(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
              >
                <option value="white">White</option>
                <option value="black">Black</option>
              </select>
            </div>
          </div>

          {/* Checkboxes */}
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={addCaptions}
                onChange={(e) => setAddCaptions(e.target.checked)}
                className="rounded"
              />
              Add captions (subtitles)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={skipProofread}
                onChange={(e) => setSkipProofread(e.target.checked)}
                className="rounded"
              />
              Skip AI proofreading
            </label>
          </div>

          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
              {error}
            </p>
          )}

          {status && loading && (
            <p className="text-sm text-blue-600 bg-blue-50 px-3 py-2 rounded">
              {status}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 px-4 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? status || "Processing..." : "Create Short Videos"}
          </button>
        </form>
      </main>
    </>
  );
}
