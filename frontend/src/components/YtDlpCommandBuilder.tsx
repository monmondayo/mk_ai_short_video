"use client";

import { useMemo, useState } from "react";

/**
 * yt-dlp コマンド生成ツール。
 *
 * 「ダウンロードしたいけど毎回フォーマット文字列を忘れる」問題用の
 * 小さなユーティリティ。URL を貼ってプリセットを選ぶと、ターミナルに
 * そのまま貼れるコマンドが表示され、ワンクリックでクリップボードに
 * コピーできる。
 *
 * デフォルトの「mkvideo 互換」プリセットは pipeline/download.py の
 * ydl_opts と同じ引数を組み立てる —— Web UI を使わずにローカル CLI
 * で同じ素材動画を得たいときにそのまま流用できる。
 */

type Preset = {
  id: string;
  label: string;
  description: string;
  format: string;
  /** yt-dlp の --merge-output-format (MP4 にまとめたいケース用) */
  mergeOutputFormat?: string;
  /** true なら -x --audio-format mp3 を付ける */
  audioOnly?: boolean;
  audioFormat?: string;
};

const PRESETS: Preset[] = [
  {
    id: "mkvideo",
    label: "mkvideo 互換 (推奨)",
    description:
      "パイプラインと同じ引数。mp4 にマージされて pipeline/download.py と同じ素材が得られる。",
    format: "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    mergeOutputFormat: "mp4",
  },
  {
    id: "best-mp4",
    label: "最高画質 (MP4)",
    description: "画質優先で mp4 にマージ。容量は大きくなる。",
    format: "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
    mergeOutputFormat: "mp4",
  },
  {
    id: "720p-mp4",
    label: "720p 以下 (MP4)",
    description: "容量を抑えたいとき向け。",
    format:
      "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b[height<=720]",
    mergeOutputFormat: "mp4",
  },
  {
    id: "1080p-mp4",
    label: "1080p 以下 (MP4)",
    description: "フル HD までに制限。",
    format:
      "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[height<=1080]",
    mergeOutputFormat: "mp4",
  },
  {
    id: "audio-mp3",
    label: "音声のみ (MP3)",
    description: "音声だけ抜き出して mp3 に変換する。",
    format: "bestaudio/best",
    audioOnly: true,
    audioFormat: "mp3",
  },
  {
    id: "best",
    label: "Best available (なんでも OK)",
    description: "yt-dlp のデフォルトに任せる。拡張子は動画によって変わる。",
    format: "best",
  },
];

type OutputTemplate = {
  id: string;
  label: string;
  template: string;
  description: string;
};

const OUTPUT_TEMPLATES: OutputTemplate[] = [
  {
    id: "title",
    label: "タイトル",
    template: "%(title)s.%(ext)s",
    description: "人間向け。日本語タイトルそのまま。",
  },
  {
    id: "id",
    label: "動画 ID",
    template: "%(id)s.%(ext)s",
    description: "pipeline/download.py と同じ。スクリプト向け。",
  },
  {
    id: "channel-title",
    label: "チャンネル/タイトル",
    template: "%(uploader)s - %(title)s.%(ext)s",
    description: "複数チャンネルを落とすとき用。",
  },
];

/**
 * シェル単一引用符でエスケープ。URL に ' や & や ? が混ざっても
 * POSIX sh でそのまま貼れる形にする。
 */
function shellSingleQuote(s: string): string {
  if (s === "") return "''";
  return `'${s.split("'").join("'\\''")}'`;
}

export default function YtDlpCommandBuilder() {
  const [url, setUrl] = useState("");
  const [presetId, setPresetId] = useState(PRESETS[0].id);
  const [templateId, setTemplateId] = useState(OUTPUT_TEMPLATES[0].id);
  const [outputDir, setOutputDir] = useState("");
  const [writeSubs, setWriteSubs] = useState(false);
  const [embedMetadata, setEmbedMetadata] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);

  const preset = PRESETS.find((p) => p.id === presetId) ?? PRESETS[0];
  const template =
    OUTPUT_TEMPLATES.find((t) => t.id === templateId) ?? OUTPUT_TEMPLATES[0];

  const command = useMemo(() => {
    const parts: string[] = ["yt-dlp"];
    parts.push("-f", shellSingleQuote(preset.format));
    if (preset.mergeOutputFormat) {
      parts.push("--merge-output-format", preset.mergeOutputFormat);
    }
    if (preset.audioOnly) {
      parts.push("-x");
      if (preset.audioFormat) {
        parts.push("--audio-format", preset.audioFormat);
      }
    }

    // 出力テンプレート。ディレクトリが指定されていれば連結する。
    const outTemplate = outputDir.trim()
      ? `${outputDir.replace(/\/$/, "")}/${template.template}`
      : template.template;
    parts.push("-o", shellSingleQuote(outTemplate));

    if (writeSubs) {
      parts.push("--write-subs", "--sub-langs", "ja,en", "--embed-subs");
    }
    if (embedMetadata) {
      parts.push("--embed-metadata");
    }

    // URL は最後。空ならプレースホルダを出す（コピーして貼る前に差し替える想定）。
    const target = url.trim() || "<URL>";
    parts.push(shellSingleQuote(target));

    return parts.join(" ");
  }, [preset, template, outputDir, writeSubs, embedMetadata, url]);

  const isPlaceholder = url.trim() === "";

  const handleCopy = async () => {
    setCopyError(null);
    try {
      if (!navigator.clipboard) {
        throw new Error("Clipboard API is not available in this browser");
      }
      await navigator.clipboard.writeText(command);
      setCopied(true);
      // 2秒後に表示を戻す。
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      setCopyError(
        e instanceof Error ? e.message : "Failed to copy to clipboard",
      );
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">yt-dlp コマンド生成</h1>
        <p className="text-sm text-gray-500 mt-1">
          YouTube の URL を貼ると、PC で動画をダウンロードするための
          <code className="mx-1 px-1 bg-gray-100 rounded">yt-dlp</code>
          コマンドを組み立てます。ターミナルにコピー＆貼り付けで実行してください。
          <br />
          未インストールなら{" "}
          <code className="px-1 bg-gray-100 rounded">
            brew install yt-dlp
          </code>{" "}
          または{" "}
          <code className="px-1 bg-gray-100 rounded">
            pip install -U yt-dlp
          </code>
          。
        </p>
      </div>

      {/* URL */}
      <div>
        <label
          htmlFor="ytdl-url"
          className="block text-sm font-medium text-gray-700 mb-1"
        >
          YouTube URL
        </label>
        <input
          id="ytdl-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=..."
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm"
          autoFocus
        />
        {isPlaceholder && (
          <p className="text-xs text-gray-400 mt-1">
            URL が未入力の場合はコマンドに <code>&lt;URL&gt;</code>{" "}
            と入れて生成します（コピー後に手動で置き換え可能）。
          </p>
        )}
      </div>

      {/* Preset */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          画質 / フォーマット
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {PRESETS.map((p) => (
            <label
              key={p.id}
              className={`flex items-start gap-2 p-3 border rounded-lg cursor-pointer select-none transition-colors ${
                p.id === presetId
                  ? "border-blue-500 bg-blue-50"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
            >
              <input
                type="radio"
                name="preset"
                value={p.id}
                checked={p.id === presetId}
                onChange={() => setPresetId(p.id)}
                className="mt-1"
              />
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900">
                  {p.label}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {p.description}
                </p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Output template */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          ファイル名テンプレート
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {OUTPUT_TEMPLATES.map((t) => (
            <label
              key={t.id}
              className={`flex items-start gap-2 p-3 border rounded-lg cursor-pointer select-none transition-colors ${
                t.id === templateId
                  ? "border-blue-500 bg-blue-50"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
            >
              <input
                type="radio"
                name="template"
                value={t.id}
                checked={t.id === templateId}
                onChange={() => setTemplateId(t.id)}
                className="mt-1"
              />
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900">
                  {t.label}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">{t.description}</p>
                <p className="text-xs text-gray-400 mt-0.5 font-mono truncate">
                  {t.template}
                </p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Output dir */}
      <div>
        <label
          htmlFor="ytdl-outdir"
          className="block text-sm font-medium text-gray-700 mb-1"
        >
          保存先ディレクトリ (任意)
        </label>
        <input
          id="ytdl-outdir"
          type="text"
          value={outputDir}
          onChange={(e) => setOutputDir(e.target.value)}
          placeholder="~/Downloads または /path/to/dir"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm font-mono"
        />
        <p className="text-xs text-gray-400 mt-1">
          空欄ならターミナルの現在ディレクトリに保存されます。
        </p>
      </div>

      {/* Extras */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          オプション
        </label>
        <div className="space-y-2">
          <label className="flex items-start gap-2 p-3 border border-gray-200 rounded-lg cursor-pointer select-none hover:bg-gray-50">
            <input
              type="checkbox"
              checked={writeSubs}
              onChange={(e) => setWriteSubs(e.target.checked)}
              className="mt-0.5"
            />
            <div>
              <p className="text-sm font-medium text-gray-900">
                字幕を埋め込む (日本語 + 英語)
              </p>
              <p className="text-xs text-gray-500">
                <code>--write-subs --sub-langs ja,en --embed-subs</code>
                。YouTube が字幕を提供している動画のみ有効。
              </p>
            </div>
          </label>
          <label className="flex items-start gap-2 p-3 border border-gray-200 rounded-lg cursor-pointer select-none hover:bg-gray-50">
            <input
              type="checkbox"
              checked={embedMetadata}
              onChange={(e) => setEmbedMetadata(e.target.checked)}
              className="mt-0.5"
            />
            <div>
              <p className="text-sm font-medium text-gray-900">
                メタデータを埋め込む
              </p>
              <p className="text-xs text-gray-500">
                <code>--embed-metadata</code>。タイトル・投稿者・説明を
                コンテナに埋め込む。
              </p>
            </div>
          </label>
        </div>
      </div>

      {/* Command output */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="block text-sm font-medium text-gray-700">
            生成されたコマンド
          </label>
          <div className="flex items-center gap-3">
            {copied && (
              <span className="text-xs text-green-600">コピーしました</span>
            )}
            <button
              type="button"
              onClick={handleCopy}
              className="px-3 py-1.5 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 inline-flex items-center gap-2"
            >
              <svg
                className="w-4 h-4"
                viewBox="0 0 20 20"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M7 2a2 2 0 00-2 2v12a2 2 0 002 2h8a2 2 0 002-2V6.414A2 2 0 0016.414 5L13 1.586A2 2 0 0011.586 1H7zm0 2h4v3a1 1 0 001 1h3v9H7V4z" />
                <path d="M3 6a2 2 0 012-2v12a2 2 0 002 2H5a2 2 0 01-2-2V6z" />
              </svg>
              クリップボードへコピー
            </button>
          </div>
        </div>
        <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all">
          {command}
        </pre>
        {copyError && (
          <p className="text-xs text-red-600 mt-1">
            コピー失敗: {copyError}（コマンド欄を選択して
            <kbd className="mx-1 px-1 border rounded">Cmd+C</kbd>
            でも OK）
          </p>
        )}
      </div>
    </div>
  );
}
