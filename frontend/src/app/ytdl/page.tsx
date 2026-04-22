import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase-server";
import Navbar from "@/components/Navbar";
import YtDlpCommandBuilder from "@/components/YtDlpCommandBuilder";

/**
 * yt-dlp コマンド生成ユーティリティページ。
 *
 * 既存の dashboard / jobs パターンと同じく Server Component で認証
 * チェックを行い、実際の UI は Client Component に委ねる。
 */
export default async function YtDlpPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) redirect("/login");

  return (
    <>
      <Navbar email={user.email} />
      <main className="max-w-3xl mx-auto w-full px-4 py-8">
        <YtDlpCommandBuilder />
      </main>
    </>
  );
}
