import { redirect } from "next/navigation";
import Link from "next/link";
import { createClient } from "@/lib/supabase-server";
import Navbar from "@/components/Navbar";
import JobCard from "@/components/JobCard";
import { fetchR2Usage, type R2UsageResult } from "@/lib/modal-api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}

function R2UsageBar({ usage }: { usage: R2UsageResult }) {
  const pct = Math.min(usage.usage_percent, 100);
  const barColor =
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-yellow-400" : "bg-green-500";
  const textColor =
    pct >= 90 ? "text-red-600" : pct >= 70 ? "text-yellow-600" : "text-green-600";

  return (
    <div className="bg-white border border-gray-200 rounded-xl px-5 py-4 mb-6">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-gray-700">
          Cloudflare R2 ストレージ
        </span>
        <span className={`text-sm font-semibold ${textColor}`}>
          {usage.usage_percent.toFixed(1)}%
        </span>
      </div>
      <div className="w-full bg-gray-100 rounded-full h-2.5 mb-2">
        <div
          className={`h-2.5 rounded-full ${barColor} transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>
          {formatBytes(usage.total_bytes)} 使用中 ({usage.total_objects.toLocaleString()} オブジェクト)
        </span>
        <span>無料枠 {formatBytes(usage.free_limit_bytes)}</span>
      </div>
    </div>
  );
}

export default async function DashboardPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) redirect("/login");

  const [{ data: jobs }, r2Usage] = await Promise.all([
    supabase.from("jobs").select("*").order("created_at", { ascending: false }),
    fetchR2Usage({ cache: "no-store" }).catch(() => null),
  ]);

  return (
    <>
      <Navbar email={user.email} />
      <main className="max-w-5xl mx-auto w-full px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Jobs</h1>
          <Link
            href="/jobs/new"
            className="px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
          >
            + New Job
          </Link>
        </div>

        {r2Usage && <R2UsageBar usage={r2Usage} />}

        {!jobs || jobs.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-gray-500 mb-4">No jobs yet</p>
            <Link
              href="/jobs/new"
              className="text-blue-600 hover:underline"
            >
              Create your first short video
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        )}
      </main>
    </>
  );
}
