# mkvideo クラウドデプロイ セットアップガイド

## 全体構成

```
                   ┌──────────────────────────────────┐
                   │     Frontend (Vercel / Next.js)   │
                   └──┬──────────┬──────────┬──────────┘
                      │          │          │
              動画アップロード  API呼び出し  リアルタイム通知
              (署名URL経由)     │          │
                      │          │          │
                      ▼          ▼          ▼
              Cloudflare R2   Modal     Supabase
              (ファイル保存)  (処理)   (DB + Auth)
                      ▲          │          ▲
                      │          │          │
                      └──────────┴──────────┘
                       読み書き     ステータス更新
```

**処理フロー:**
1. ユーザーが動画をブラウザからアップロード → R2 に直接保存 (署名URL)
2. Modal が R2 から動画を取得 → Whisper 文字起こし → Claude 校正
3. ユーザーがフロントエンドでトランスクリプトを編集
4. Modal が Claude でストーリー抽出 → ffmpeg でショート動画をレンダリング → R2 に保存
5. フロントエンドで完成動画をダウンロード

> **YouTube動画の場合**: ModalのIPはYouTubeにブロックされるため、
> ローカルで `yt-dlp` ダウンロード → R2 アップロード → Modal 処理 の流れになります。

必要なアカウント: **Modal**, **Supabase**, **Cloudflare**, **Anthropic**

---

## 1. Anthropic API キー (ANTHROPIC_API_KEY)

Claude API (校正 + ストーリー抽出) に必要。

1. https://console.anthropic.com/ にアクセス
2. アカウント作成 or ログイン
3. 左メニュー **「API Keys」** をクリック
4. **「Create Key」** をクリック
5. 名前をつけて作成 (例: `mkvideo-cloud`)
6. 表示されるキーをコピー: `sk-ant-api03-xxxxx...`

> **注意**: キーは一度しか表示されません。必ずコピーして安全な場所に保存してください。

```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
```

---

## 2. Supabase (SUPABASE_URL, SUPABASE_SERVICE_KEY)

ユーザー認証、ジョブ管理DB、リアルタイム進捗通知に使用。

### 2-1. プロジェクト作成

1. https://supabase.com/ にアクセス → **「Start your project」**
2. GitHub アカウントでログイン
3. **「New Project」** をクリック
4. 設定を入力:
   - **Organization**: 自分の組織名 (初回は自動作成される)
   - **Project name**: `mkvideo`
   - **Database password**: 強力なパスワードを設定 (メモしておく)
   - **Region**: `Northeast Asia (Tokyo)` を選択
5. **「Create new project」** をクリック → 数分待つ

### 2-2. URL と API キーの取得

1. プロジェクトダッシュボードが表示されたら、左メニュー **「Project Settings」** (歯車アイコン)
2. **「API」** タブをクリック
3. 以下の2つをコピー:

| 項目 | 場所 | 例 |
|------|------|-----|
| **Project URL** | 「URL」セクション | `https://abcdefg.supabase.co` |
| **service_role key** | 「Project API keys」→ `service_role` の `Reveal` をクリック | `eyJhbGciOiJIUzI1NiIs...` |

> **2025年以降の新規プロジェクト**: `service_role` の代わりに **「Secret API Keys」** タブで `sb_secret_...` 形式のキーが使われる場合があります。その場合はそちらを使用してください。

> **注意**: `service_role` キーは **サーバーサイド専用** です。フロントエンドのコードには絶対に含めないでください。

```
SUPABASE_URL=https://abcdefg.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxx
```

### 2-3. データベーステーブル作成

1. Supabase ダッシュボード → 左メニュー **「SQL Editor」**
2. **「New Query」** をクリック
3. `supabase/migrations/002_drop_and_recreate.sql` の内容をすべてコピー＆ペースト
4. **「Run」** をクリック → 「Success. No rows returned」と表示されれば成功

> **初回セットアップの場合**: `002_drop_and_recreate.sql` を使ってください。
> これは既存テーブルがあれば DROP してから作り直すため、初回でも再セットアップでも安全に使えます。
> `001_create_tables.sql` は参考用です。

作成されるテーブル:

| テーブル | 説明 |
|---------|------|
| `jobs` | ジョブ管理 (ステータス、進捗、パラメータ) |
| `transcripts` | Whisper文字起こし結果 (セグメント配列、編集可能) |
| `stories` | Claude抽出ストーリー定義 |
| `output_videos` | 完成動画のR2リンク、尺、サイズ |

セキュリティ:
- **RLS (Row Level Security)** が全テーブルで有効 — ユーザーは自分のデータのみアクセス可能
- Modal ワーカーは `service_role` キーで RLS をバイパス
- **Realtime** が `jobs` テーブルで有効 — フロントエンドが `progress` の変更をリアルタイム受信

### 2-4. Realtime の有効化確認

1. ダッシュボード → **「Database」** → **「Replication」**
2. `supabase_realtime` の Source で `jobs` テーブルにチェックが入っていることを確認
3. 入っていなければチェックを入れて保存

### 2-5. Authentication 設定

1. ダッシュボード → **「Authentication」** → **「Providers」**
2. **Email** が有効になっていることを確認 (デフォルトで有効)
3. 必要に応じて **Google** / **GitHub** OAuth も有効化:
   - Google: GCP Console で OAuth クライアント ID を作成、Supabase に登録
   - GitHub: GitHub Settings → Developer settings → OAuth Apps

---

## 3. Cloudflare R2 (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME)

動画・画像・JSONファイルの保存に使用。無料枠: 10GB ストレージ, 転送料無料。

### 3-1. Cloudflare アカウント確認

1. https://dash.cloudflare.com/ にログイン

### 3-2. Account ID の取得

1. ダッシュボード右上の URL を確認:
   `https://dash.cloudflare.com/<Account_ID>/...`
2. または左メニュー **「概要 (Overview)」** ページの右側に表示される **「アカウント ID」** をコピー

```
R2_ACCOUNT_ID=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

### 3-3. R2 バケット作成

1. 左メニュー **「R2 オブジェクトストレージ」** をクリック
2. **「バケットを作成」** をクリック
3. 設定:
   - **バケット名**: `mkvideo`
   - **ロケーション**: 「アジア太平洋 (APAC)」を選択
4. **「バケットを作成」** をクリック

```
R2_BUCKET_NAME=mkvideo
```

### 3-4. API トークン (Access Key / Secret Key) の作成

1. R2 のページ上部にある **「R2 APIトークンの管理」** をクリック
2. **「APIトークンを作成する」** をクリック
3. 設定:
   - **トークン名**: `mkvideo-api`
   - **権限**: **「オブジェクトの読み取りと書き込み」** を選択
   - **バケットの指定**: 「特定のバケットにのみ適用する」→ `mkvideo` を選択
   - **TTL**: 指定しない (無期限) ※ 必要に応じて有効期限を設定
4. **「APIトークンを作成する」** をクリック
5. 表示される 2つの値をコピー:
   - **アクセス キー ID**: `xxxxxxxxxxxxxxxxx`
   - **シークレット アクセス キー**: `yyyyyyyyyyyyyyyyyyyy`

> **注意**: シークレット アクセス キーは **この画面でしか表示されません**。必ずコピーしてください。紛失した場合はトークンを再作成する必要があります。

```
R2_ACCESS_KEY_ID=xxxxxxxxxxxxxxxxx
R2_SECRET_ACCESS_KEY=yyyyyyyyyyyyyyyyyyyy
```

### 3-5. CORS 設定 (フロントエンドからの直接アップロードに必要)

フロントエンドからブラウザ経由で R2 に動画をアップロードするため、CORS (Cross-Origin Resource Sharing) を設定する必要があります。

**方法 A: Cloudflare ダッシュボードから設定**

1. **R2 オブジェクトストレージ** → バケット `mkvideo` をクリック
2. **「設定」** タブをクリック
3. **「CORS ポリシー」** セクションで **「編集」** をクリック
4. 以下の JSON を貼り付けて **「保存」**:

```json
[
  {
    "AllowedOrigins": [
      "http://localhost:3000",
      "https://*.vercel.app"
    ],
    "AllowedMethods": ["GET", "PUT", "HEAD"],
    "AllowedHeaders": ["Content-Type", "Content-Length"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

> **本番ドメイン追加**: Vercel にカスタムドメインを設定した場合、`AllowedOrigins` にそのドメインも追加してください。
> 例: `"https://mkvideo.yourdomain.com"`

**方法 B: AWS CLI (S3互換) から設定**

```bash
# AWS CLI を S3互換モードで R2 に接続
aws s3api put-bucket-cors \
  --bucket mkvideo \
  --cors-configuration file://cloudflare/r2-cors.json \
  --endpoint-url https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com
```

### 3-6. CORS 設定確認

```bash
# preflight リクエストをシミュレート
curl -I -X OPTIONS \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: PUT" \
  "https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com/mkvideo/test"
```

レスポンスに `Access-Control-Allow-Origin: http://localhost:3000` が含まれていれば成功です。

---

## 4. Modal (サーバーレス実行環境)

Whisper (GPU) + ffmpeg (CPU) の重い処理を実行。月 $30 無料クレジット。

### 4-1. アカウント作成

1. https://modal.com/ にアクセス → **「Sign Up」**
2. GitHub アカウントでログイン
3. 無料枠 ($30/月) が自動適用される

### 4-2. CLI インストール & 認証

```bash
pip install modal
modal setup
```

`modal setup` を実行するとブラウザが開きます。ログインして認証を許可してください。
ターミナルに `✓ Token saved` と表示されれば成功です。

### 4-3. 動作確認

```bash
modal run --help
```

エラーなく表示されればOKです。

---

## 5. すべてのシークレットを Modal に登録

取得した全キーを1つの Modal Secret にまとめて登録します。

```bash
modal secret create mkvideo-secrets \
  ANTHROPIC_API_KEY="sk-ant-api03-xxxxxxxxxxxxx" \
  SUPABASE_URL="https://abcdefg.supabase.co" \
  SUPABASE_SERVICE_KEY="eyJhbGciOiJIUzI1NiIs..." \
  R2_ACCOUNT_ID="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6" \
  R2_ACCESS_KEY_ID="xxxxxxxxxxxxxxxxx" \
  R2_SECRET_ACCESS_KEY="yyyyyyyyyyyyyyyyyyyy" \
  R2_BUCKET_NAME="mkvideo"
```

登録確認:
```bash
modal secret list
```

`mkvideo-secrets` が表示されればOK。

> **値を変更したい場合**: 同じコマンドを再実行すると上書きされます。

---

## 6. デプロイ

### 6-1. Modal デプロイ (バックエンド)

```bash
# プロジェクトルートから実行
modal deploy mkvideo/cloud/modal_app.py
```

成功すると以下のように表示されます:
```
✓ Created objects.
├── 🔨 Created transcribe_video_job.
├── 🔨 Created extract_and_render.
└── 🔨 Created api => https://your-workspace--mkvideo-api.modal.run
```

表示される URL (`https://your-workspace--mkvideo-api.modal.run`) をメモしてください。

### 6-2. Vercel デプロイ (フロントエンド)

**方法 A: Vercel CLI (推奨)**

```bash
# Vercel CLI インストール
npm install -g vercel

# frontend ディレクトリからデプロイ
cd frontend
vercel
```

初回は対話式でプロジェクト設定が求められます:
- **Link to existing project?** → No (新規)
- **Project name** → `mkvideo`
- **Framework** → Next.js (自動検出)
- **Root Directory** → `./` (frontend ディレクトリ内で実行しているため)

**方法 B: GitHub 連携 (自動デプロイ)**

1. https://vercel.com/ にログイン
2. **「Add New」** → **「Project」** → GitHub リポジトリを選択
3. **Root Directory** を `frontend` に設定
4. **「Deploy」** をクリック

### 6-3. Vercel 環境変数の設定

Vercel ダッシュボード → プロジェクト → **「Settings」** → **「Environment Variables」** で以下を追加:

| 変数名 | 値 | 例 |
|--------|-----|-----|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase プロジェクト URL | `https://abcdefg.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase の **anon** キー (※ service_role ではない) | `eyJhbGci...` |
| `NEXT_PUBLIC_MODAL_API_URL` | Modal の API URL (6-1 で取得) | `https://xxx--mkvideo-api.modal.run` |

> **Supabase anon key の場所**: Supabase ダッシュボード → Project Settings → API → `anon` の `public` キー

設定後、**「Redeploy」** を実行してください。

### 6-4. ローカル開発用 .env.local

```bash
cd frontend
cp .env.local.example .env.local
```

`.env.local` を編集:
```
NEXT_PUBLIC_SUPABASE_URL=https://abcdefg.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGci...
NEXT_PUBLIC_MODAL_API_URL=https://xxx--mkvideo-api.modal.run
```

```bash
npm run dev
# → http://localhost:3000
```

---

## 7. セットアップ検証

デプロイ前に、設定が正しいか検証スクリプトで確認できます:

```bash
# プロジェクトルートで実行 (mkvideo conda 環境)
PYTHONPATH=. python scripts/verify_setup.py
```

環境変数、Supabase接続、R2バケットアクセス、パッケージ import、ffmpeg をチェックします。
全項目が `PASS` になればデプロイ準備完了です。

---

## 8. 動作テスト

デプロイ後のテスト手順:

### 8-1. ローカルで動画をダウンロード → R2 にアップロード

> **注意**: Modal のデータセンター IP は YouTube にブロックされるため、
> 動画のダウンロードはローカルマシンで行い、R2 にアップロードしてから Modal に処理を依頼します。

```bash
# .env に R2 の認証情報が設定されていることを確認してから実行
PYTHONPATH=. python -m mkvideo.cloud.upload_helper test-001 "https://youtu.be/k67ewV1YU_E"
```

出力例:
```
[Upload] Downloading video from YouTube...
[Upload] Downloaded: video.mp4 (150.3 MB)
[Upload] Uploading to R2 (jobs/test-001/source/video.mp4)...
[Upload] Upload complete: jobs/test-001/source/video.mp4
```

### 8-2. Modal に文字起こしジョブを投入

```bash
# Job 投入 (Phase 1: R2の動画を文字起こし)
curl -X POST https://your-workspace--mkvideo-api.modal.run/submit-job \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "test-001",
    "whisper_model": "base",
    "whisper_language": "ja"
  }'

# 返却される call_id でステータス確認
curl https://your-workspace--mkvideo-api.modal.run/job-status/<call_id>
```

### 8-3. フロントエンドからの動画アップロード (署名URL方式)

```bash
# 1. アップロード用の署名URLを取得
curl -X POST https://your-workspace--mkvideo-api.modal.run/upload-url \
  -H "Content-Type: application/json" \
  -d '{"job_id": "test-002", "filename": "video.mp4"}'

# 2. 返却された upload_url に動画を PUT
curl -X PUT "<upload_url>" \
  -H "Content-Type: video/mp4" \
  --data-binary @video.mp4
```

### 8-4. フロントエンド (ブラウザ) からの E2E テスト

1. `http://localhost:3000` (ローカル) or Vercel URL にアクセス
2. メールアドレスで **Sign Up** → 確認メール → ログイン
3. **「+ New Job」** をクリック
4. 動画ファイルを選択してアップロード → パラメータ設定 → **「Create Short Videos」**
5. ジョブ詳細画面で進捗がリアルタイム表示される
6. `awaiting_review` になったらトランスクリプトを編集 → **「Save & Continue」**
7. レンダリング完了後、完成動画が一覧表示される

---

## トラブルシューティング

| 問題 | 解決策 |
|------|--------|
| `modal setup` でブラウザが開かない | `modal token new` を試す |
| シークレット値を間違えた | `modal secret create mkvideo-secrets KEY=NEW_VALUE` で上書き |
| R2 シークレットキーを紛失 | Cloudflare ダッシュボードでトークンを削除 → 再作成 |
| Supabase service_role が見つからない | 「Project Settings」→「API」→ `service_role` の `Reveal` ボタン |
| Supabase anon key が見つからない | 「Project Settings」→「API」→ `anon` の `public` キー (フロントエンド用) |
| Modal デプロイが遅い | 初回は Whisper モデルのダウンロード (~1.5GB) があるため 5-10分かかる場合あり |
| YouTube が Modal からブロックされる | **仕様**。動画はローカルでダウンロードして R2 にアップロードしてください (`python -m mkvideo.cloud.upload_helper`) |
| Modal で `ModuleNotFoundError: mkvideo` | `modal deploy` をプロジェクトルートから実行しているか確認。`modal.Mount` がローカルの `mkvideo/` をマウントします |
| フロントエンドから R2 アップロードが CORS エラー | セクション 3-5 の CORS 設定を確認。`AllowedOrigins` にフロントエンドの URL が含まれているか確認 |
| Supabase の SQL が `column "user_id" does not exist` | `002_drop_and_recreate.sql` を使ってください (001 ではなく) |
| Realtime で進捗が更新されない | Supabase ダッシュボード → Database → Replication で `jobs` テーブルが有効か確認 |
| `verify_setup.py` で環境変数が FAIL | プロジェクトルートの `.env` に各キーが設定されているか確認 |

---

## 費用見積もり (月 5-10本の動画処理)

| サービス | 無料枠 | 月額 |
|---------|--------|------|
| Modal | $30/月 | $0 (無料枠内) |
| Supabase | 500MB DB, 50K MAU | $0 (無料枠内) |
| Cloudflare R2 | 10GB, 転送料無料 | $0 (無料枠内) |
| Vercel | 100GB帯域 | $0 (Hobby プラン) |
| Anthropic API | なし (従量課金) | ~$2-5 |
| **合計** | | **~$2-5/月** |

---

## ローカル CLI も引き続き使えます

クラウドデプロイしても、ローカル CLI は今まで通り動作します:

```bash
# mkvideo conda 環境で実行
./run.sh "https://youtu.be/xxxxx" -n 5 --duration 30-45 --output-dir output

# オプション一覧
./run.sh --help
```
