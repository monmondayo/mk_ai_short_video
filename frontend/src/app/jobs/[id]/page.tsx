import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase-server";
import Navbar from "@/components/Navbar";
import JobDetailClient from "./JobDetailClient";

export default async function JobDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) redirect("/login");

  // Fetch job
  const { data: job } = await supabase
    .from("jobs")
    .select("*")
    .eq("id", id)
    .single();

  if (!job) redirect("/dashboard");

  // Fetch transcript (if exists)
  const { data: transcript } = await supabase
    .from("transcripts")
    .select("*")
    .eq("job_id", id)
    .single();

  // Fetch output videos (if any)
  const { data: videos } = await supabase
    .from("output_videos")
    .select("*")
    .eq("job_id", id)
    .order("rank");

  return (
    <>
      <Navbar email={user.email} />
      <main className="max-w-4xl mx-auto w-full px-4 py-8">
        <JobDetailClient
          job={job}
          transcript={transcript}
          videos={videos || []}
        />
      </main>
    </>
  );
}
