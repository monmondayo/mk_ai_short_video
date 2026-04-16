import { redirect } from "next/navigation";
import Link from "next/link";
import { createClient } from "@/lib/supabase-server";
import Navbar from "@/components/Navbar";
import JobCard from "@/components/JobCard";

export default async function DashboardPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) redirect("/login");

  const { data: jobs } = await supabase
    .from("jobs")
    .select("*")
    .order("created_at", { ascending: false });

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
