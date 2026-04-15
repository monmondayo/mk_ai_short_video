# mkvideo クラウドデプロイ セットアップガイド

## 全体構成

```
Frontend (Vercel)  →  Modal (処理)  →  Cloudflare R2 (ファイル保存)
                   →  Supabase (DB + Auth + リアルタイム通知)
```

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

```bash
# プロジェクトルートから実行
modal deploy mkvideo/cloud/modal_app.py
```

成功すると以下のように表示されます:
```
✓ Created objects.
├── 🔨 Created download_and_transcribe.
├── 🔨 Created extract_and_render.
└── 🔨 Created api => https://your-workspace--mkvideo-api.modal.run
```

表示される URL が Web API のエンドポイントです。
Next.js フロントエンドからこの URL を呼び出します。

---

## 7. 動作テスト

デプロイ後、API を直接叩いてテスト:

```bash
# Job 投入 (Phase 1: ダウンロード + 文字起こし)
curl -X POST https://your-workspace--mkvideo-api.modal.run/submit-job \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "test-001",
    "youtube_url": "https://youtu.be/k67ewV1YU_E",
    "whisper_model": "base",
    "whisper_language": "ja"
  }'

# 返却される call_id でステータス確認
curl https://your-workspace--mkvideo-api.modal.run/job-status/<call_id>
```

---

## トラブルシューティング

| 問題 | 解決策 |
|------|--------|
| `modal setup` でブラウザが開かない | `modal token new` を試す |
| シークレット値を間違えた | `modal secret create mkvideo-secrets KEY=NEW_VALUE` で上書き |
| R2 シークレットキーを紛失 | Cloudflare ダッシュボードでトークンを削除 → 再作成 |
| Supabase service_role が見つからない | 「Project Settings」→「API」→ `service_role` の `Reveal` ボタン |
| Modal デプロイが遅い | 初回は Whisper モデルのダウンロード (~1.5GB) があるため 5-10分かかる場合あり |

---

## 費用見積もり (月 5-10本の動画処理)

| サービス | 月額 |
|---------|------|
| Modal ($30 無料枠内) | $0 |
| Supabase (無料枠) | $0 |
| Cloudflare R2 (無料枠) | $0 |
| Anthropic API | ~$2-5 |
| **合計** | **~$2-5** |
