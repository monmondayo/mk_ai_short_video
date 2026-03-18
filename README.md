# mkvideo — YouTube Long Video → 10 Short Videos

clip.opus.pro / AlphaCut のようにYouTube URLを入力するだけでショート動画を自動生成するツール。

## 処理フロー

```
YouTube URL
  → ① yt-dlp でダウンロード
  → ② Whisper で文字起こし
  → ③ Claude API でハイライト抽出（10箇所）
  → ④ ffmpeg でクリップ + 9:16 リフレーム
  → ⑤ 字幕焼き込み
  → output/ に 10本のショート動画
```

## セットアップ

### 1. Conda環境の作成

このプロジェクトは **conda環境 (mkvideo)** を使用します。初回セットアップ時に環境を作成してください。

```bash
# conda環境を作成
conda create -n mkvideo python=3.11 -y

# 依存関係をインストール
conda activate mkvideo
pip install -r requirements.txt
```

**インストールされる主な依存関係:**
- `yt-dlp>=2024.1.1` - YouTube動画のダウンロード
- `openai-whisper>=20231117` - 音声文字起こし
- `anthropic>=0.20.0` - Claude APIクライアント
- `torch`, `numpy`, `numba` - Whisperの依存関係

### 2. APIキーの設定

```bash
# 環境変数として設定
export ANTHROPIC_API_KEY="sk-ant-..."

# または .env ファイルに記載（推奨）
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > .env
```

**注意:** `run.sh` スクリプトは自動的に `mkvideo` conda環境を使用します。手動でactivateする必要はありません。

## 使い方

**注意:** `run.sh` は自動的に `mkvideo` conda環境を使用します。

```bash
# 基本（10本生成）
./run.sh "https://www.youtube.com/watch?v=XXXXXXXX"

# オプション指定
./run.sh "https://www.youtube.com/watch?v=XXXXXXXX" \
  --num-clips 10 \
  --whisper-model small \
  --output-dir my_output

# ローカル動画ファイルから生成（ダウンロードスキップ）
./run.sh "dummy_url" --skip-download /path/to/video.mp4

# 字幕なし
./run.sh "https://www.youtube.com/watch?v=XXXXXXXX" --no-captions
```

## オプション

| オプション | デフォルト | 説明 |
|---|---|---|
| `-n`, `--num-stories` | 10 | 生成するクリップ数 |
| `--duration` | `30-60` | 1本あたりの秒数範囲 (`0-15`/`15-30`/`30-45`/`30-60`/`45-60`/`60-90`) |
| `--whisper-model` | `base` | Whisperモデルサイズ (tiny/base/small/medium/large) |
| `--bg-color` | `white` | 背景色 (`white`/`black`) |
| `--no-captions` | false | 字幕オーバーレイを無効化 |
| `--output-dir` | `output/` | 出力ディレクトリ |
| `--skip-download` | - | ローカルファイルを使用 |

## Whisperモデル選択ガイド

| モデル | 速度 | 精度 | 推奨用途 |
|---|---|---|---|
| `tiny` | 最速 | 低 | テスト用 |
| `base` | 速い | 普通 | デフォルト |
| `small` | 普通 | 良い | 本番推奨 |
| `medium` | 遅い | 高い | 高品質が必要な場合 |
| `large` | 最遅 | 最高 | 最高品質 |

## 出力

`output/` ディレクトリに以下の形式でファイルが生成されます：

```
output/
  01_Surprising_fact_about_AI.mp4
  02_The_key_to_success.mp4
  ...
  10_Final_takeaway.mp4
```

- 解像度: 1080×1920 (9:16 縦型)
- 字幕: 自動焼き込み
- 長さ: 15〜60秒/本

## システム要件・依存ツール

### 必須ツール
- **ffmpeg** - 動画処理
  ```bash
  brew install ffmpeg
  ```
- **conda** (miniconda/anaconda) - Python環境管理
  - [Miniconda公式サイト](https://docs.conda.io/en/latest/miniconda.html)からインストール
- **Anthropic APIキー** - Claude API利用

### Python依存関係（requirements.txt）
以下のパッケージが自動的にインストールされます：
- `yt-dlp>=2024.1.1` - YouTube動画ダウンロード
- `openai-whisper>=20231117` - 音声文字起こし（AI）
- `anthropic>=0.20.0` - Claude APIクライアント
- その他の依存関係: `torch`, `numpy`, `numba`, `tiktoken`, `tqdm` など

### 動作環境
- Python 3.11
- macOS / Linux（Windows未テスト）
- conda環境: `mkvideo`
