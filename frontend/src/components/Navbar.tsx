"use client";

import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";

export default function Navbar({ email }: { email?: string }) {
  const router = useRouter();
  const supabase = createClient();

  const handleLogout = async () => {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  };

  return (
    <nav className="bg-white border-b border-gray-200 px-6 py-3">
      <div className="max-w-5xl mx-auto flex items-center justify-between">
        <div className="flex items-center gap-6">
          <a href="/dashboard" className="text-xl font-bold text-gray-900">
            mkvideo
          </a>
          <a
            href="/ytdl"
            className="text-sm text-gray-600 hover:text-gray-900"
            title="yt-dlp コマンド生成"
          >
            yt-dlp
          </a>
        </div>
        <div className="flex items-center gap-4">
          {email && (
            <span className="text-sm text-gray-500">{email}</span>
          )}
          <button
            onClick={handleLogout}
            className="text-sm text-gray-600 hover:text-gray-900"
          >
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}
