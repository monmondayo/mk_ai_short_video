# mkvideo — YouTube 長尺動画 → ショート動画ジェネレーター

clip.opus.pro / AlphaCut のように、長尺動画から AI がハイライトを抽出して 9:16 のショート動画を自動生成するツール。

**2つの使い方:**
- **ローカル CLI** — 手元のマシンで全処理を完結。`./run.sh <url>` 一発。
- **クラウド Web UI** — ブラウザから動画をアップロードして Vercel / Supabase / Modal / R2 上で処理。途中でトランスクリプトとストーリーを編集できる。

## 処理フロー

```
動画 (YouTube URL or ローカルファイル or ブラウザアップロード)
  → ① yt-dlp でダウンロード (ローカルCLIのみ)
  → ② Whisper で文字起こし
  → ③ Claude で校正 (省略可)
  → ④ [レビュー] トランスクリプトを編集 (Web UI / --review-transcript)
  → ⑤ Claude でストーリー抽出
  → ⑥ [レビュー] タイトル / フック / テーマ / セグメント本文を編集 (Web UI)
  → ⑦ ffmpeg + Pillow でクリップ結合・9:16 リフレーム・字幕焼き込み
  → 完成したショート動画 (output/ または R2 + ダウンロードリンク)
```

---

## A. ローカル CLI

### 1. Conda 環境のセットアップ

```bash
# conda 環境を作成
conda create -n mkvideo python=3.11 -y
conda activate mkvideo
pip install -r requirements.txt
```

**主な依存:**
- `yt-dlp>=2024.1.1` — YouTube 動画ダウンロード
- `openai-whisper>=20231117` — 音声文字起こし
- `anthropic>=0.20.0` — Claude API (校正 + ストーリー抽出)
- `Pillow>=10.0.0` — テキストオーバーレイ PNG 生成
- `Janome>=0.5.0` — 日本語字幕の改行処理

### 2. API キーの設定

```bash
# .env ファイルに記載 (推奨)
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > .env

# または環境変数として
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 3. 使い方

```bash
# 基本 (10本生成)
./run.sh "https://www.youtube.com/watch?v=XXXXXXXX"

# オプション指定
./run.sh "https://www.youtube.com/watch?v=XXXXXXXX" \
  --num-stories 5 \
  --duration 30-60 \
  --whisper-model medium \
  --whisper-language ja \
  --bg-color white

# ローカル動画ファイルから生成 (ダウンロードスキップ)
./run.sh "dummy_url" --skip-download /path/to/video.mp4

# トランスクリプトを手動で確認・修正してから続行
./run.sh "https://..." --review-transcript

# 字幕 (下部) を無効化 (上部のフック文字は常に表示)
./run.sh "https://..." --no-captions

# Claude 校正をスキップ (Whisper の素の出力を使用)
./run.sh "https://..." --no-proofread

# Cloudflare R2 の容量確認 (10GB 無料枠チェック)
./run.sh --check-r2-usage

# 特定プレフィックス配下のみ確認 (例: jobs/)
./run.sh --check-r2-usage --r2-prefix jobs
```

> `--check-r2-usage` は `boto3` を使用するため、未導入の場合は `pip install -r requirements-cloud.txt` を先に実行してください。

> `run.sh` は `python -m mkvideo` を conda 環境で呼ぶラッパーです。conda 以外を使う場合は直接 `python -m mkvideo <url> ...` を実行してください。

### CLI オプション一覧

| オプション | デフォルト | 説明 |
|---|---|---|
| `url` (positional) | — | YouTube URL (または `--skip-download` 時のダミー) |
| `-n`, `--num-stories` | `10` | 生成するショート動画の本数 |
| `--duration` | `30-60` | 1本あたりの秒数範囲 (`0-15` / `15-30` / `30-45` / `30-60` / `45-60` / `60-90`) |
| `--whisper-model` | `medium` | Whisper モデル (`tiny` / `base` / `small` / `medium` / `large`) |
| `--whisper-language` | `ja` | Whisper の言語ヒント (`en` で英語、空文字で自動検出) |
| `--no-proofread` | off | Claude による校正をスキップ |
| `--review-transcript` | off | 文字起こし後に一時停止して手動編集 (エディタは `$EDITOR`) |
| `--review-subtitles` | off | 字幕 (SRT) 生成後に一時停止して手動編集 |
| `--bg-color` | `white` | 背景色 (`white` / `black`) |
| `--no-captions` | off | 下部字幕オーバーレイを無効化 |
| `--output-dir` | `output` | 出力ディレクトリ |
| `--skip-download` | — | ローカル動画パスを指定してダウンロードをスキップ |
| `--check-r2-usage` | off | Cloudflare R2 容量を集計して無料枠判定して終了 |
| `--r2-prefix` | 空 | 容量集計対象を特定プレフィックス配下に限定 |
| `--free-limit-gb` | `10.0` | 無料枠判定に使う上限 GB 値 |

### Whisper モデル選択ガイド

| モデル | 速度 | 精度 | 推奨用途 |
|---|---|---|---|
| `tiny` | 最速 | 低 | テスト用 |
| `base` | 速い | 普通 | 軽量マシン |
| `small` | 普通 | 良い | CPU でのバランス型 |
| `medium` | 遅い | 高い | **デフォルト** / 実用本番 |
| `large` | 最遅 | 最高 | 最高品質 (GPU 推奨) |

### 出力

`output/` ディレクトリに以下の形式で生成されます:

```
output/
  01_<タイトル>.mp4
  02_<タイトル>.mp4
  ...
  10_<タイトル>.mp4
```

- 解像度: 1080×1920 (9:16 縦型)
- 上部: フック文字 (黒 + 黄色アウトライン)
- 下部: 自動字幕 (白 + ピンクアウトライン、`--no-captions` で無効化可)
- 中間 JSON (transcript, stories) は `temp/` にキャッシュされ再実行時に再利用される

---

## B. クラウド Web UI (Vercel + Supabase + Modal + R2)

ブラウザから動画をアップロードして、処理状況をリアルタイムで追い、トランスクリプトとストーリーを編集してからレンダリングできる。

### アーキテクチャ

```
                   ┌──────────────────────────────────┐
                   │     Frontend (Vercel / Next.js)   │
                   └──┬──────────┬──────────┬──────────┘
                      │          │          │
              動画アップロード  API呼び出し  リアルタイム通知
              (署名URL経由)     │          │
                      ▼          ▼          ▼
              Cloudflare R2   Modal     Supabase
              (ファイル保存)  (処理)   (DB + Auth + Realtime)
```

| レイヤー | 役割 |
|---|---|
| **Vercel (Next.js 16)** | 認証・ジョブ作成フォーム・進捗表示・トランスクリプト編集・ストーリー編集・ダウンロードリンク |
| **Supabase** | ユーザー認証、ジョブ / トランスクリプト / ストーリー / 出力動画のメタデータ、Realtime 進捗通知、RLS |
| **Modal** | Whisper 文字起こし + Claude 校正 + ストーリー抽出 + ffmpeg レンダリング (GPU/CPU を秒課金) |
| **Cloudflare R2** | 元動画 + 出力動画の保管。署名URL経由でブラウザから直接アップロード / ダウンロード |
| **Anthropic API** | Claude による校正とストーリー抽出 |

### Web UI のワークフロー

1. ログイン → 「新規ジョブ」で動画ファイルをアップロード (署名URL経由で直接 R2 へ)
2. ジョブ詳細画面で進捗をリアルタイム追跡 (Supabase Realtime + 4秒ポーリングフォールバック)
3. `awaiting_review` になったら **トランスクリプトエディタ** で誤字修正・セグメント分割/結合
4. 「保存してストーリー抽出を開始」をクリック → Claude がストーリーを抽出
5. `awaiting_story_review` になったら **ストーリーエディタ** で編集:
   - **タイトル** (動画中央の文字) ✍️
   - **フック** (動画上部の大きな文字) ✍️
   - **テーマ** (メモ用・動画には描画されない) ✍️
   - **セグメント説明** (メモ用) ✍️
   - タイムスタンプは固定 (ffmpeg カット位置と同期が必要)
6. 「Save & Render」でレンダリング開始 → 完了したら各動画の **View** / **Download** リンクが表示される
7. 不要になったジョブはジョブ一覧のゴミ箱アイコンから削除 (R2 のファイルも一緒にクリーンアップ)

### セットアップ

詳細は **[SETUP_CLOUD.md](./SETUP_CLOUD.md)** を参照。大まかな手順:

1. **Anthropic** の API キーを発行
2. **Supabase** プロジェクトを作成 → SQL Editor で `supabase/migrations/002_drop_and_recreate.sql` を実行 (初回) → `003_add_awaiting_story_review_status.sql` を実行 (既存環境のアップデート)
3. **Cloudflare R2** バケットを作成 + S3 互換アクセスキー発行 + CORS 設定
4. **Modal** に登録 → `modal token new` → `modal deploy mkvideo/cloud/modal_app.py`
5. Modal シークレットに Anthropic / Supabase / R2 のキーを登録
6. `frontend/` の `.env.local` に Supabase 公開キーと Modal API URL を設定
7. Vercel に frontend を deploy (`vercel --prod` または GitHub 連携)

### クラウド用の追加依存 (Modal 側)

`requirements-cloud.txt` に記載:
```
modal>=0.64.0
boto3>=1.34.0
requests>=2.31.0
fastapi>=0.110.0
pydantic>=2.0.0
```

`requirements.txt` (Whisper / Claude / Pillow / Janome) と合わせて Modal イメージに焼き込まれる。

---

## システム要件

### ローカル CLI
- **ffmpeg** — `brew install ffmpeg` (macOS) / `apt install ffmpeg` (Linux)
- **conda** (miniconda / anaconda) — Python 環境管理
- Python 3.11
- Anthropic API キー
- macOS / Linux (Windows 未テスト)

### クラウド Web UI
- Node.js 20+ (frontend ローカル開発時)
- 上記 4 サービスのアカウント (Anthropic / Supabase / Cloudflare / Modal)
- frontend の開発には `node_modules/next/dist/docs/` も参照 (**Next.js 16 の breaking changes**; `frontend/AGENTS.md` を参照)

---

## プロジェクト構成

```
mkvideo/                    # Python パッケージ (CLI + クラウド共通のパイプライン)
  cli.py                    # CLI エントリポイント
  __main__.py               # python -m mkvideo で実行
  pipeline/                 # コアロジック (ローカル・クラウド共通)
    download.py             # yt-dlp ダウンロード
    transcribe.py           # Whisper + Claude 校正 + レビュー入出力
    stories.py              # Claude ストーリー抽出
    render.py               # ffmpeg + Pillow レンダリング
    text_layout.py          # 日本語禁則・レイアウト
    fonts.py                # フォント検出 (macOS + Linux)
    constants.py            # 定数 (解像度、色、秒数範囲など)
  storage/                  # ストレージ抽象化
    local.py                # ローカル FS (CLI 用)
    r2.py                   # Cloudflare R2 (クラウド用)
  cloud/
    modal_app.py            # Modal 関数 + FastAPI Web エンドポイント
    supabase_client.py      # 進捗更新 + DB 読み書き

frontend/                   # Next.js 16 App Router
  src/app/
    jobs/[id]/              # ジョブ詳細・進捗
    jobs/[id]/review/       # トランスクリプト編集
    jobs/new/               # 新規ジョブ作成
    dashboard/              # ジョブ一覧
  src/components/
    TranscriptEditor.tsx
    StoryEditor.tsx         # タイトル / フック / テーマ / セグメント本文を編集
    ProgressDisplay.tsx     # Realtime + ポーリングフォールバック
    VideoResults.tsx        # View / Download リンク
    JobCard.tsx             # 削除ボタン付き

supabase/migrations/        # DB スキーマ
  001_create_tables.sql
  002_drop_and_recreate.sql # 再セットアップ時はこちら
  003_add_awaiting_story_review_status.sql  # 既存環境のアップデート

scripts/                    # セットアップ補助
cloudflare/                 # R2 CORS 設定ファイル
SETUP_CLOUD.md              # クラウドデプロイ詳細手順
```

---

## トラブルシューティング (クラウド版)

| 症状 | 原因 / 対処 |
|---|---|
| ダッシュボードでジョブが `uploading` のまま止まる | ブラウザ→R2 のアップロードが失敗したまま DB が更新されなかった。ジョブ一覧のゴミ箱アイコンで削除し、再アップロード。 |
| `Transcribing with Whisper` から進まないが、裏ではすでに進行済み | Supabase Realtime イベント取りこぼし。`ProgressDisplay` が 4 秒ポーリングで自動追従するので数秒待つ、または画面をリロード。 |
| 保存時に `400 Bad Request` on `/rest/v1/jobs` | `jobs.status` CHECK 制約が古い。`supabase/migrations/003_add_awaiting_story_review_status.sql` を SQL Editor で実行。 |
| `AttributeError: 'function' object has no attribute 'tqdm'` (Whisper) | `whisper` パッケージを属性アクセスで import すると関数に解決されてしまう問題。`mkvideo/pipeline/transcribe.py` で `importlib.import_module("whisper.transcribe")` を使うように修正済み。Modal を再デプロイ。 |
| `OSError: [Errno 36] File name too long` (R2 ダウンロード時) | 長い日本語タイトル + boto3 の一時サフィックスで 255 バイトを超過。`_download_video_from_r2` でローカルファイル名を `source<ext>` に固定する修正済み。Modal を再デプロイ。 |
| ダウンロードした動画が 9KB の HTML ファイル | 旧実装で R2 の相対キーを直接開いていた。現在は Modal の `/download-url` が署名URL (`ResponseContentDisposition=attachment`) を返す。Modal を再デプロイして新しい Web UI を使う。 |
| 動画上部に `.mp4` が焼き込まれる | `video_title` に拡張子が残っていた。`modal_app.py` でレンダリング前に正規表現で拡張子を除去済み。Modal を再デプロイ。 |

## ライセンス

個人用プロジェクト。
