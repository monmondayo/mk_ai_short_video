#!/usr/bin/env python3
"""
mkvideo - YouTube Long Video → 10 Story-based Short Videos
Each story = multiple segments joined with fade/dissolve transitions
Layout: solid-color background (black/white), video letterboxed center,
        title+hook text above, transcript subtitles below.
Usage: python main.py <youtube_url> [options]
"""

import argparse
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import anthropic
import whisper
import yt_dlp
from dotenv import load_dotenv
from janome.tokenizer import Tokenizer
from PIL import Image, ImageDraw, ImageFont


# ──────────────────────────────────────────
# Layout constants (pixels)
# ──────────────────────────────────────────
TARGET_W, TARGET_H = 1080, 1920
# 16:9 source fills full width at 1080px → height = 1080×9/16 = 608px.
# Top band (title+hook) is smaller so the video sits near the top.
# Bottom band (subtitles+images) gets the remaining space.
INNER_H      = round(TARGET_W * 9 / 16)         # 608  — video display area (16:9)
TOP_ZONE_H   = 400                               # smaller top band → less blank above
BOTTOM_ZONE_H = TARGET_H - INNER_H - TOP_ZONE_H # 912  — larger bottom band
LOWER_Y      = TARGET_H - BOTTOM_ZONE_H         # 1008 — y-position of lower text area

# Text overlay PNGs are pinned to the video edge (not the screen edge).
TEXT_PNG_H  = 300
UPPER_PNG_Y = TOP_ZONE_H - TEXT_PNG_H           # 100  — upper PNG top y

# How many subtitle entries before switching the image set in the lower zone
IMG_CHANGE_INTERVAL = 3

# Gap (px) between adjacent image rows and the subtitle strip
IMG_GAP = 20
MIN_SUBTITLE_FRAME_DURATION = 1 / 30

# ──────────────────────────────────────────
# Pipeline constants
# ──────────────────────────────────────────
DEFAULT_NUM_STORIES = 10
MAX_STORY_DURATION = 90
MIN_STORY_DURATION = 20
MIN_SEGMENT_DURATION = 5
MAX_SEGMENT_DURATION = 45
TRANSITION_DURATION = 0.5
MIN_SEGMENT_GAP = 0.05
DURATION_TOLERANCE_SEC = 15
TRANSCRIPT_CACHE_VERSION = 2
SUBTITLE_MIN_CUE_DURATION = 1.0
SUBTITLE_PREFERRED_CUE_DURATION = 2.8
SUBTITLE_MAX_CUE_DURATION = 4.2
SUBTITLE_MAX_CUE_CHARS = 42
SUBTITLE_MIN_CUE_CHARS = 20
SUBTITLE_WORD_GAP_SEC = 0.60
# Characters forbidden from starting a subtitle entry (Japanese kinsoku rules).
# "ー" and small kana should never appear at the head of a new line/entry.
_SUBTITLE_NO_START = frozenset("ーぁぃぅぇぉっゃゅょァィゥェォッャュョ")
# Characters that cannot END a subtitle cue — the following token is inseparable.
# っ/ッ (促音) must always be followed by the consonant it doubles.
_CUE_NO_END = frozenset("っッ")
# Counter/unit characters that must not be separated from a preceding digit.
_COUNTER_CHARS = frozenset("万億兆千百十円個本台枚人回度分秒年月日時間週冊匹頭羽杯着件点棟")

TEMP_DIR = Path("temp")

# Load .env from project root so API keys are available when launched via run.sh
PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")
JANOME_TOKENIZER = Tokenizer()

XFADE_TYPE = {
    "dissolve": ("dissolve",  TRANSITION_DURATION),
    "fade":     ("fadeblack", TRANSITION_DURATION),
    "cut":      ("fade",      0.05),
}

STROKE_W = 8
TEXT_GAP = 100                          # gap between video edge and text PNG

# Upper text: black + yellow stroke
UPPER_FG     = (0,   0,   0)
UPPER_STROKE = (255, 220,  0)

# Lower text (subtitles): white + pink stroke
LOWER_FG     = (255, 255, 255)
LOWER_STROKE = (255,  20, 147)

# (min_sec, max_sec) for each duration preset
DURATION_PRESETS: dict[str, tuple[int, int]] = {
    "0-15":  (5,  15),
    "15-30": (15, 30),
    "30-45": (30, 45),
    "30-60": (30, 60),   # default
    "45-60": (45, 60),
    "60-90": (60, 90),
}


# ──────────────────────────────────────────
# Step 1: Download
# ──────────────────────────────────────────
def download_video(url: str, out_dir: Path) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
    }
    print(f"\n[1/5] Downloading: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info["id"]
        video_title = info.get("title", "")
        video_path = out_dir / f"{video_id}.mp4"
        if not video_path.exists():
            candidates = list(out_dir.glob(f"{video_id}.*"))
            if not candidates:
                raise FileNotFoundError(f"Downloaded file not found for id={video_id}")
            video_path = candidates[0]
    print(f"   Saved: {video_path}  ({video_title})")
    return video_path, video_title


def fetch_video_title(url: str) -> str:
    """Fetch YouTube video title without downloading (used with --skip-download)."""
    try:
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "")
    except Exception:
        return ""


# ──────────────────────────────────────────
# Step 2: Transcribe
# ──────────────────────────────────────────
def transcribe_video(video_path: Path, model_name: str = "medium", language: str | None = "ja") -> dict:
    lang_hint = f", language={language}" if language else ""
    print(f"\n[2/5] Transcribing with Whisper ({model_name}{lang_hint})…")
    model = whisper.load_model(model_name)
    kwargs: dict = {"verbose": False, "word_timestamps": True}
    if language:
        kwargs["language"] = language
    result = model.transcribe(str(video_path), **kwargs)
    segments = [
        {
            "start": s["start"],
            "end": s["end"],
            "text": s["text"].strip(),
            "words": [
                {
                    "start": w.get("start", s["start"]),
                    "end": w.get("end", s["end"]),
                    "word": w.get("word", "").strip(),
                }
                for w in s.get("words", [])
                if w.get("word", "").strip()
            ],
        }
        for s in result["segments"]
    ]
    print(f"   Total segments: {len(segments)}")
    return {"text": result["text"], "segments": segments}


# ──────────────────────────────────────────
# Step 2.5: Proofread transcript with Claude
# ──────────────────────────────────────────
def proofread_transcript(transcript: dict) -> dict:
    """Use Claude to fix kanji/grammar errors caused by speech recognition."""
    print("   Proofreading transcript with Claude Haiku…")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("   ANTHROPIC_API_KEY not set, skipping proofreading.")
        return transcript

    client = anthropic.Anthropic(api_key=api_key)
    segments = transcript["segments"]
    CHUNK_SIZE = 40
    CONTEXT_SIZE = 5  # segments before/after chunk sent as context only
    corrected_segments: list[dict] = []

    for chunk_start in range(0, len(segments), CHUNK_SIZE):
        chunk = segments[chunk_start : chunk_start + CHUNK_SIZE]
        texts = [s["text"] for s in chunk]

        # Build context: a few preceding segments to help disambiguation
        ctx_before = [s["text"] for s in segments[max(0, chunk_start - CONTEXT_SIZE):chunk_start]]
        context_note = ""
        if ctx_before:
            context_note = "【前の文脈】\n" + "\n".join(ctx_before) + "\n\n"

        user_content = (
            f"{context_note}"
            f"【校正対象】\n{json.dumps(texts, ensure_ascii=False)}"
        )
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=(
                "あなたは日本語音声認識（Whisper）テキストの専門校正者です。\n"
                "以下の誤りを修正してください：\n"
                "- 同音異義語の誤認識（例：「機械」→「器械」、「以上」→「異常」、「制度」→「精度」など）\n"
                "- 助詞の誤り（は/が/を/に/で/へ の取り違え）\n"
                "- 漢字の誤変換（読みは合っているが意味が違う漢字）\n"
                "- 句読点の脱落や誤挿入\n"
                "- 外来語・カタカナの誤認識（例：「テクノロジー」→「テクノロジ」）\n"
                "- フィラー語（「えー」「あのー」「そのー」）は保持してよい\n"
                "前の文脈がある場合はそれを参考に話題・固有名詞を正確に判断してください。\n"
                "話者の意図・口調・文体を変えずに自然な日本語に校正してください。\n"
                "【校正対象】のJSON配列のみ校正し、同じ要素数のJSON配列で返してください。"
                "説明や他のテキストは不要です。出力はJSON配列のみ。"
            ),
            messages=[{"role": "user", "content": user_content}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            corrected_texts = json.loads(raw.strip())
            if len(corrected_texts) != len(texts):
                # Count mismatch → index would shift: fall back to original for this chunk
                print(f"   Warning: chunk {chunk_start // CHUNK_SIZE + 1} returned "
                      f"{len(corrected_texts)} items for {len(texts)} segments — skipping")
                corrected_segments.extend(chunk)
            else:
                for seg, fixed in zip(chunk, corrected_texts):
                    corrected_seg = {
                        "start": seg["start"],
                        "end": seg["end"],
                        "text": fixed,
                    }
                    if seg.get("words"):
                        corrected_seg["words"] = seg["words"]
                    corrected_segments.append(corrected_seg)
        except (json.JSONDecodeError, IndexError):
            corrected_segments.extend(chunk)

    print(f"   Proofread {len(corrected_segments)} segments")
    return {
        "text": "".join(s["text"] for s in corrected_segments),
        "segments": corrected_segments,
    }


# ──────────────────────────────────────────
# Step 3: Extract Stories via Claude
# ──────────────────────────────────────────
STORY_SYSTEM = """\
You are a viral short-form video editor who creates story-driven content.
Given a transcript with timestamps, identify compelling "stories" — each story
is composed of 2–5 scattered segments from the video that together form a
complete narrative arc (setup → development → payoff).

Rules:
- Each story must have a clear narrative: beginning, middle, end
- 2 to 5 segments per story (segments from different parts of the video are fine)
- Total story duration: {min_total}–{max_total} seconds
- Each individual segment: {min_seg}–{max_seg} seconds
- No two stories should be nearly identical
- Segments within a story must NOT overlap in time
- For each segment after the first, specify transition_in:
    "dissolve" = smooth crossfade (same topic continues)
    "fade"     = fade to black then in (scene/topic changes)
    "cut"      = instant hard cut (punchy, fast-paced)
- hook: short punchy sentence shown as large text ABOVE the video (max 60 chars)
- title, theme, and hook MUST be written in the SAME language as the transcript content
  (e.g., if the transcript is in Japanese, write all titles/hooks in Japanese)
- Output JSON only — no explanation, no markdown fences

Output format:
{{
  "stories": [
    {{
      "rank": 1,
      "title": "Catchy title max 60 chars",
      "theme": "One sentence: what narrative arc this tells",
      "hook": "Short punchy hook sentence, max 60 chars",
      "segments": [
        {{
          "start": 12.5,
          "end": 38.0,
          "description": "Setup: introduces the problem"
        }},
        {{
          "start": 245.0,
          "end": 272.0,
          "transition_in": "dissolve",
          "description": "Development: key insight revealed"
        }},
        {{
          "start": 890.2,
          "end": 915.0,
          "transition_in": "fade",
          "description": "Payoff: surprising conclusion"
        }}
      ]
    }}
  ]
}}
"""


def extract_stories(
    transcript: dict,
    num_stories: int = DEFAULT_NUM_STORIES,
    video_duration: float | None = None,
    min_dur: int = MIN_STORY_DURATION,
    max_dur: int = MAX_STORY_DURATION,
) -> list[dict]:
    print(f"\n[3/5] Extracting {num_stories} stories with Claude…")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in environment or in .env file."
        )
    client = anthropic.Anthropic(api_key=api_key)
    lines = [
        f"[{s['start']:.1f}s–{s['end']:.1f}s] {s['text']}"
        for s in transcript["segments"]
    ]
    duration_note = f"Total video duration: {video_duration:.0f}s\n" if video_duration else ""
    # Derive per-segment limits from total duration range
    min_seg = max(3, min_dur // 4)
    max_seg = min(max_dur // 2, MAX_SEGMENT_DURATION)
    system = STORY_SYSTEM.format(
        min_total=min_dur, max_total=max_dur,
        min_seg=min_seg, max_seg=max_seg,
    )
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": (
            f"{duration_note}Create exactly {num_stories} stories.\n\nTRANSCRIPT:\n"
            + "\n".join(lines)
        )}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    stories = json.loads(raw.strip())["stories"]
    print(f"   Created {len(stories)} stories:")
    for s in stories:
        segs = s["segments"]
        total = sum(sg["end"] - sg["start"] for sg in segs)
        trs = [sg.get("transition_in", "-") for sg in segs[1:]]
        print(f"   #{s['rank']:2d} {s['title'][:45]:<45} {len(segs)}segs {total:.0f}s tr={trs}")
    return stories


# ──────────────────────────────────────────
# ffmpeg helpers
# ──────────────────────────────────────────
def get_video_info(video_path: Path) -> dict:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    return {"width": int(vs["width"]), "height": int(vs["height"]),
            "duration": float(info["format"]["duration"])}


def get_clip_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def build_letterbox_filter(bg_color: str) -> str:
    return (
        f"scale={TARGET_W}:{INNER_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{INNER_H}:(ow-iw)/2:(oh-ih)/2:color={bg_color},"
        f"pad={TARGET_W}:{TARGET_H}:0:{TOP_ZONE_H}:color={bg_color}"
    )


def wrap_text(text: str, max_chars: int = 22) -> list[str]:
    """Wrap text at max_chars. Splits on spaces when present, else on Janome morpheme
    boundaries (Japanese/CJK). Tokens that exceed max_chars are hard-wrapped at the char boundary."""
    def hard_wrap(s: str) -> list[str]:
        chunks: list[str] = []
        while len(s) > max_chars:
            chunks.append(s[:max_chars])
            s = s[max_chars:]
        if s:
            chunks.append(s)
        return chunks

    if " " in text:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            if len(word) > max_chars:
                # token itself is too long — flush current line then hard-wrap the token
                if current:
                    lines.append(current)
                    current = ""
                chunks = hard_wrap(word)
                lines.extend(chunks[:-1])
                current = chunks[-1]
            elif current and len(current) + 1 + len(word) > max_chars:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines
    else:
        # Japanese/CJK: wrap at Janome morpheme boundaries
        tokens = [t.surface for t in JANOME_TOKENIZER.tokenize(text) if t.surface]
        if not tokens:
            return hard_wrap(text)
        lines_ja: list[str] = []
        current = ""
        current_tokens_ja: list[str] = []
        for token in tokens:
            if len(current) + len(token) <= max_chars:
                current += token
                current_tokens_ja.append(token)
            else:
                # Short token (≤2 chars): pull back the previous token so that
                # functional morphemes (ます、て、た、帯 …) never start a line alone.
                if len(token) <= 2 and len(current_tokens_ja) >= 2:
                    last = current_tokens_ja.pop()
                    current = "".join(current_tokens_ja)
                    lines_ja.append(current)
                    combined = last + token
                    current = combined
                    current_tokens_ja = [combined]
                else:
                    if current:
                        lines_ja.append(current)
                    if len(token) > max_chars:
                        chunks = hard_wrap(token)
                        lines_ja.extend(chunks[:-1])
                        current = chunks[-1]
                        current_tokens_ja = [current] if current else []
                    else:
                        current = token
                        current_tokens_ja = [token]
        if current:
            lines_ja.append(current)
        return lines_ja


def run_ff(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )


# ──────────────────────────────────────────
# Step 4: Cut segments (letterboxed, no text)
# ──────────────────────────────────────────
def cut_segment(src: Path, start: float, end: float, dst: Path, bg_color: str) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-i", str(src), "-t", str(end - start),
        "-vf", build_letterbox_filter(bg_color),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", str(dst),
    ]
    run_ff(cmd)


# ──────────────────────────────────────────
# Step 5: Join segments with transitions
# ──────────────────────────────────────────
def join_segments(clips: list[Path], transitions: list[str], out_path: Path) -> None:
    n = len(clips)
    if n == 1:
        shutil.copy(clips[0], out_path)
        return
    durations = [get_clip_duration(p) for p in clips]
    vf_parts: list[str] = []
    af_parts: list[str] = []
    video_offset = 0.0
    for i in range(n - 1):
        xfade_name, t = XFADE_TYPE.get(transitions[i], ("dissolve", TRANSITION_DURATION))
        t = min(t, durations[i] * 0.9, durations[i + 1] * 0.9)
        src_v = f"[{i}:v]" if i == 0 else f"[vx{i-1}]"
        src_a = f"[{i}:a]" if i == 0 else f"[ax{i-1}]"
        video_offset += durations[i] - t
        vf_parts.append(
            f"{src_v}[{i+1}:v]xfade=transition={xfade_name}"
            f":duration={t:.3f}:offset={video_offset:.3f}[vx{i}]"
        )
        af_parts.append(f"{src_a}[{i+1}:a]acrossfade=d={t:.3f}[ax{i}]")
    run_ff(
        ["ffmpeg", "-y"]
        + [arg for p in clips for arg in ["-i", str(p)]]
        + ["-filter_complex", ";".join(vf_parts + af_parts),
           "-map", f"[vx{n-2}]", "-map", f"[ax{n-2}]",
           "-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
           str(out_path)]
    )


def estimate_joined_duration(segment_durations: list[float], transitions: list[str]) -> float:
    if not segment_durations:
        return 0.0
    total = segment_durations[0]
    for i in range(1, len(segment_durations)):
        transition = transitions[i - 1] if i - 1 < len(transitions) else "dissolve"
        _, t = XFADE_TYPE.get(transition, ("dissolve", TRANSITION_DURATION))
        t = min(t, segment_durations[i - 1] * 0.9, segment_durations[i] * 0.9)
        total += segment_durations[i] - t
    return total


def enforce_story_duration_range(
    stories: list[dict],
    min_total: int,
    max_total: int,
    video_duration: float,
    tolerance_sec: int = 0,
) -> int:
    """Normalize story segments so estimated joined duration stays within min_total-max_total."""
    allowed_min = max(0, min_total - tolerance_sec)
    allowed_max = max_total + tolerance_sec
    min_seg = max(3, min_total // 4)
    max_seg = min(max_total // 2, MAX_SEGMENT_DURATION)
    changed_count = 0

    for story in stories:
        original_segments = story.get("segments", [])
        if not original_segments:
            continue

        normalized: list[dict] = []
        for raw in sorted(original_segments, key=lambda s: float(s.get("start", 0.0))):
            start = max(0.0, min(float(raw.get("start", 0.0)), max(video_duration - 0.1, 0.0)))
            end = max(start + min_seg, min(float(raw.get("end", start + min_seg)), video_duration))

            if end - start > max_seg:
                end = start + max_seg

            if normalized and start < normalized[-1]["end"] + MIN_SEGMENT_GAP:
                start = normalized[-1]["end"] + MIN_SEGMENT_GAP
                end = max(end, start + min_seg)
                if end > video_duration:
                    end = video_duration

            if end - start < min_seg or start >= video_duration:
                continue

            item = dict(raw)
            item["start"] = round(start, 3)
            item["end"] = round(end, 3)
            normalized.append(item)

        if not normalized:
            continue

        transitions = [s.get("transition_in", "dissolve") for s in normalized[1:]]

        def durations() -> list[float]:
            return [s["end"] - s["start"] for s in normalized]

        est = estimate_joined_duration(durations(), transitions)

        # Trim when too long
        guard = 0
        while est > allowed_max + 0.01 and guard < 20 and normalized:
            overflow = est - allowed_max
            for idx in range(len(normalized) - 1, -1, -1):
                d = normalized[idx]["end"] - normalized[idx]["start"]
                reducible = max(0.0, d - min_seg)
                if reducible <= 0:
                    continue
                delta = min(reducible, overflow)
                normalized[idx]["end"] = round(normalized[idx]["end"] - delta, 3)
                overflow -= delta
                if overflow <= 0:
                    break

            est = estimate_joined_duration(durations(), transitions)
            if est > allowed_max + 0.01 and len(normalized) > 1:
                normalized.pop()
                transitions = [s.get("transition_in", "dissolve") for s in normalized[1:]]
                est = estimate_joined_duration(durations(), transitions)
            guard += 1

        # Extend when too short
        if est < allowed_min - 0.01:
            deficit = allowed_min - est
            for idx in range(len(normalized) - 1, -1, -1):
                d = normalized[idx]["end"] - normalized[idx]["start"]
                seg_cap = min(max_seg, video_duration - normalized[idx]["start"])
                extendable = max(0.0, seg_cap - d)
                if extendable <= 0:
                    continue
                delta = min(extendable, deficit)
                normalized[idx]["end"] = round(normalized[idx]["end"] + delta, 3)
                deficit -= delta
                if deficit <= 0:
                    break

            if deficit > 0 and normalized:
                extra = min(deficit, video_duration - normalized[-1]["end"])
                normalized[-1]["end"] = round(normalized[-1]["end"] + extra, 3)

        previous = json.dumps(original_segments, ensure_ascii=False, sort_keys=True)
        current = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if previous != current:
            changed_count += 1
            story["segments"] = normalized

    return changed_count


# ──────────────────────────────────────────
# Step 6: Text overlays via Pillow + ffmpeg overlay
# ──────────────────────────────────────────
INPUT_DIR = Path("input")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def scan_input_images() -> list[Path]:
    """Return sorted list of image files found in input/."""
    if not INPUT_DIR.exists():
        return []
    return sorted(p for p in INPUT_DIR.iterdir() if p.suffix.lower() in _IMAGE_EXTS)


def fit_image_in_box(img_path: Path, box_w: int, box_h: int, bg: tuple) -> Image.Image:
    """Load an image and resize/pad it to exactly box_w × box_h."""
    try:
        src = Image.open(img_path).convert("RGB")
        src.thumbnail((box_w, box_h), Image.LANCZOS)
        canvas = Image.new("RGB", (box_w, box_h), bg)
        canvas.paste(src, ((box_w - src.width) // 2, (box_h - src.height) // 2))
        return canvas
    except Exception:
        return Image.new("RGB", (box_w, box_h), bg)


def fit_text_font(
    lines: list[str], font_path: str, start_size: int, min_size: int = 24
) -> tuple[ImageFont.FreeTypeFont, int]:
    """Return (font, line_height) — the largest font ≤ start_size where every line
    fits within TARGET_W pixels (stroke included)."""
    _img = Image.new("RGB", (1, 1))
    _draw = ImageDraw.Draw(_img)
    max_w = TARGET_W - STROKE_W * 2 - 10  # 10px safety margin

    size = max(start_size, min_size)  # ensure loop runs at least once
    while size >= min_size:
        font = load_font(font_path, size)
        if all(
            _draw.textlength(line, font=font) + STROKE_W * 2 <= max_w
            for line in lines
        ):
            return font, round(size * 1.2)
        size -= 2
    font = load_font(font_path, min_size)
    return font, round(min_size * 1.2)


def _measure(text: str, font: ImageFont.FreeTypeFont) -> float:
    """Return visual width of text including stroke on both sides."""
    _d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return _d.textlength(text, font=font) + STROKE_W * 2


def wrap_pixels(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Wrap text to fit within max_w pixels per line.
    For Japanese (no spaces), wraps at Janome morpheme boundaries to avoid mid-word cuts.
    Falls back to character-by-character for tokens that individually exceed max_w."""
    if not text:
        return []

    def _char_wrap(s: str) -> list[str]:
        result: list[str] = []
        cur = ""
        for ch in s:
            candidate = cur + ch
            if _measure(candidate, font) <= max_w:
                cur = candidate
            else:
                if cur:
                    result.append(cur.rstrip())
                cur = "" if ch == " " else ch
        if cur.strip():
            result.append(cur.strip())
        return result

    if " " in text:
        return _char_wrap(text)

    # Japanese/CJK: wrap at Janome morpheme boundaries
    tokens = [t.surface for t in JANOME_TOKENIZER.tokenize(text) if t.surface]
    if not tokens:
        return _char_wrap(text)

    lines: list[str] = []
    current = ""
    current_tokens: list[str] = []
    for token in tokens:
        candidate = current + token
        if _measure(candidate, font) <= max_w:
            current = candidate
            current_tokens.append(token)
        else:
            # Short token (≤2 chars): pull back the previous token so that
            # functional morphemes (ます、て、た、帯 …) never start a line alone.
            if len(token) <= 2 and len(current_tokens) >= 2:
                last = current_tokens.pop()
                current = "".join(current_tokens)
                lines.append(current)
                combined = last + token
                current = combined
                current_tokens = [combined]
            else:
                if current:
                    lines.append(current)
                if _measure(token, font) <= max_w:
                    current = token
                    current_tokens = [token]
                else:
                    # Single token too wide → char-wrap it
                    sub = _char_wrap(token)
                    lines.extend(sub[:-1])
                    current = sub[-1] if sub else ""
                    current_tokens = [current] if current else []
    if current:
        lines.append(current)
    return lines


def wrap_and_fit(
    text: str, font_path: str, start_size: int,
    max_lines: int = 2, min_size: int = 16,
) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Find the largest font ≤ start_size where text fits in max_lines lines.
    Returns (lines, font, line_height)."""
    max_w = TARGET_W - STROKE_W * 2 - 10
    size = max(start_size, min_size)
    while size >= min_size:
        font = load_font(font_path, size)
        lines = wrap_pixels(text, font, max_w)
        if len(lines) <= max_lines:
            return lines, font, round(size * 1.2)
        size -= 2
    font = load_font(font_path, min_size)
    lines = wrap_pixels(text, font, max_w)
    return lines[:max_lines], font, round(min_size * 1.2)


def find_system_font() -> str:
    """Find a pop/bold CJK-capable font on macOS."""
    candidates = [
        # 丸ゴシック: rounded, pop/fun feel
        "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
        # Heavy gothic for impact
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        # Latin fallbacks
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/ArialHB.ttc",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    raise FileNotFoundError("No usable system font found.")


def load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(font_path, size=size)
    except Exception:
        return ImageFont.load_default()


# Map of emoji/symbols → readable text equivalents
_EMOJI_REPLACEMENTS = {
    "\u2049": "?!",   # ⁉ EXCLAMATION QUESTION MARK
    "\u203c": "!!",   # ‼ DOUBLE EXCLAMATION MARK
    "\u2757": "!",    # ❗ HEAVY EXCLAMATION MARK ORNAMENT
    "\u2753": "?",    # ❓ BLACK QUESTION MARK ORNAMENT
}
# Unicode ranges not supported by typical CJK system fonts
_UNSUPPORTED_RANGES = [
    (0xFE00, 0xFE0F),    # Variation Selectors (emoji presentation selectors)
    (0x1F000, 0x1FBFF),  # Emoji / Mahjong / Domino / etc.
    (0xE0000, 0xE01FF),  # Tags block (variation selectors supplement)
]


def sanitize_render_text(text: str) -> str:
    """Strip/replace characters that CJK system fonts cannot render."""
    result = []
    for ch in text:
        replacement = _EMOJI_REPLACEMENTS.get(ch)
        if replacement is not None:
            result.append(replacement)
            continue
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _UNSUPPORTED_RANGES):
            continue
        result.append(ch)
    return "".join(result)


def bg_rgb(bg_color: str) -> tuple[int, int, int]:
    return (255, 255, 255) if bg_color == "white" else (0, 0, 0)


def text_rgb(bg_color: str) -> tuple[int, int, int]:  # noqa: ARG001
    return (255, 255, 255)  # always white for maximum visibility


def create_upper_text_image(
    story: dict, bg_color: str, work_dir: Path, rank: int, video_title: str = ""
) -> Path:
    """
    Generate a 1080×TEXT_PNG_H PNG, overlaid at y=UPPER_PNG_Y.
    Layout (top → bottom, bottom-aligned as a block):
      [video_title small]  ← YouTube/product title
      [story title medium]
      [hook large]         ← punchy hook, up to 2 lines
    """
    VT_SIZE    = 20   # video title starting font size
    VT_GAP     = 4    # gap between video title block and story title
    TITLE_SIZE = 28   # story title starting font size
    HOOK_SIZE  = 64   # hook starting font size; auto-reduced to fit
    TITLE_GAP  = 6
    BOTTOM_PAD = 4

    font_path = find_system_font()

    # ── Compute all text/font info before layout (pixel-width based) ────
    # Hook: up to 2 lines, largest font that fits
    hook_lines, hook_font, hook_lh = wrap_and_fit(
        sanitize_render_text(story.get("hook", story["title"])), font_path, HOOK_SIZE, max_lines=2)
    hook_block_h = hook_lh * len(hook_lines)

    # Story title: up to 2 lines, largest font that fits
    title_lines, title_font, title_lh = wrap_and_fit(
        sanitize_render_text(story["title"]), font_path, TITLE_SIZE, max_lines=2, min_size=16)
    title_block_h = title_lh * len(title_lines)

    # Video title: up to 2 lines (YouTube titles can be very long)
    has_vt = bool(video_title.strip())
    if has_vt:
        vt_lines, vt_font, vt_lh = wrap_and_fit(
            sanitize_render_text(video_title), font_path, VT_SIZE, max_lines=2, min_size=12)
        vt_block = vt_lh * len(vt_lines) + VT_GAP
    else:
        vt_lines, vt_lh = [], 0
        vt_block = 0

    # ── Layout: bottom-align the whole block inside the PNG ─────────────
    total_h = vt_block + title_block_h + TITLE_GAP + hook_block_h
    y = max(TEXT_PNG_H - BOTTOM_PAD - total_h, 4)

    img  = Image.new("RGB", (TARGET_W, TEXT_PNG_H), bg_rgb(bg_color))
    draw = ImageDraw.Draw(img)

    # Draw video title lines
    if has_vt:
        for vt_line in vt_lines:
            draw.text((TARGET_W // 2, y), vt_line,
                      font=vt_font, fill=UPPER_FG, anchor="mt",
                      stroke_width=max(STROKE_W - 4, 2), stroke_fill=UPPER_STROKE)
            y += vt_lh
        y += VT_GAP

    # Draw story title lines
    for title_line in title_lines:
        draw.text((TARGET_W // 2, y), title_line,
                  font=title_font, fill=UPPER_FG, anchor="mt",
                  stroke_width=STROKE_W, stroke_fill=UPPER_STROKE)
        y += title_lh
    y += TITLE_GAP

    # Draw hook lines
    for line in hook_lines:
        draw.text((TARGET_W // 2, y), line, font=hook_font, fill=UPPER_FG, anchor="mt",
                  stroke_width=STROKE_W, stroke_fill=UPPER_STROKE)
        y += hook_lh

    path = work_dir / f"s{rank}_upper.png"
    img.save(path)
    return path


def _render_subtitle_img(text: str, bg_color: str, font_path: str) -> Image.Image:
    """Render subtitle text onto a 1080×TEXT_PNG_H image and return it.
    Font size is auto-reduced so that all text fits in 3 lines without truncation."""
    SUB_START_SIZE = 72
    TOP_PAD        = 8
    img  = Image.new("RGB", (TARGET_W, TEXT_PNG_H), bg_rgb(bg_color))
    draw = ImageDraw.Draw(img)
    clean = sanitize_render_text(text)
    lines, font, line_h = wrap_and_fit(clean, font_path, SUB_START_SIZE, max_lines=3, min_size=24)
    y = TOP_PAD
    for line in lines:
        draw.text((TARGET_W // 2, y), line, font=font, fill=LOWER_FG, anchor="mt",
                  stroke_width=STROKE_W, stroke_fill=LOWER_STROKE)
        y += line_h
    return img


def create_subtitle_png(
    text: str, bg_color: str, font_path: str, work_dir: Path, name: str
) -> Path:
    path = work_dir / name
    _render_subtitle_img(text, bg_color, font_path).save(path)
    return path


def parse_srt(srt_content: str) -> list[tuple[float, float, str]]:
    """Parse SRT string into [(start_sec, end_sec, text), …]."""
    def ts_to_sec(ts: str) -> float:
        h, m, rest = ts.strip().split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    entries: list[tuple[float, float, str]] = []
    for block in srt_content.strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            start_str, end_str = lines[1].split(" --> ")
            t0, t1 = ts_to_sec(start_str), ts_to_sec(end_str)
            text = " ".join(lines[2:]).strip()
            if text:
                entries.append((t0, t1, text))
        except (ValueError, IndexError):
            continue
    return entries


def create_subtitle_video(
    entries: list[tuple[float, float, str]],
    total_dur: float,
    bg_color: str,
    work_dir: Path,
    rank: int,
    input_images: list[Path],
) -> tuple[Path, int]:
    """
    Build a lower-zone overlay video using Pillow PNGs + ffmpeg concat.

    Without images → 1080×TEXT_PNG_H, overlaid at y = LOWER_Y + TEXT_GAP
    With images    → 1080×BOTTOM_ZONE_H (2 imgs above text, 2 below),
                     overlaid at y = LOWER_Y
                     Images rotate at each Whisper subtitle boundary.

    Returns (video_path, overlay_y).
    """
    font_path  = find_system_font()
    use_imgs   = bool(input_images)
    frame_h    = BOTTOM_ZONE_H if use_imgs else TEXT_PNG_H
    overlay_y  = LOWER_Y       if use_imgs else LOWER_Y + TEXT_GAP
    bg         = bg_rgb(bg_color)

    # Pre-compute image row geometry (two IMG_GAP gaps: above and below subtitle)
    img_row_h = (BOTTOM_ZONE_H - TEXT_PNG_H - 2 * IMG_GAP) // 2
    img_w     = TARGET_W // 2                     # 540 px per image slot

    rng = random.Random(rank)  # seeded per story → reproducible

    def pick4() -> list[Path]:
        if len(input_images) >= 4:
            return rng.sample(input_images, 4)
        return rng.choices(input_images, k=4)

    def make_frame(text: str | None, imgs: list[Path]) -> Image.Image:
        if not use_imgs:
            if text is None:
                return Image.new("RGB", (TARGET_W, frame_h), bg)
            return _render_subtitle_img(text, bg_color, font_path)

        canvas = Image.new("RGB", (TARGET_W, BOTTOM_ZONE_H), bg)
        # Top row: 2 images
        for j in range(2):
            canvas.paste(fit_image_in_box(imgs[j], img_w, img_row_h, bg), (j * img_w, 0))
        # Small gap then subtitle text
        sub_y = img_row_h + IMG_GAP
        sub_img = _render_subtitle_img(text, bg_color, font_path) if text else \
                  Image.new("RGB", (TARGET_W, TEXT_PNG_H), bg)
        canvas.paste(sub_img, (0, sub_y))
        # Small gap then bottom row: 2 images
        bot_y = sub_y + TEXT_PNG_H + IMG_GAP
        for j in range(2):
            canvas.paste(fit_image_in_box(imgs[j + 2], img_w, img_row_h, bg),
                         (j * img_w, bot_y))
        return canvas

    # ── build concat list ──────────────────────────────────────────
    current_imgs: list[Path] = pick4() if use_imgs else []
    concat_lines: list[str]  = []
    prev_end = 0.0

    for i, (t0, t1, text) in enumerate(entries):
        # gap before this entry (keep current images, no text)
        if t0 > prev_end + 0.01:
            p = work_dir / f"s{rank}_lz_b{i}.png"
            make_frame(None, current_imgs).save(p)
            blank_dur = max(t0 - prev_end, MIN_SUBTITLE_FRAME_DURATION)
            concat_lines.append(f"file '{p.resolve()}'\nduration {blank_dur:.3f}")

        # switch image set every IMG_CHANGE_INTERVAL subtitles
        if use_imgs and i % IMG_CHANGE_INTERVAL == 0:
            current_imgs = pick4()

        p = work_dir / f"s{rank}_lz_{i}.png"
        make_frame(text, current_imgs).save(p)
        text_dur = max(t1 - t0, MIN_SUBTITLE_FRAME_DURATION)
        concat_lines.append(f"file '{p.resolve()}'\nduration {text_dur:.3f}")
        prev_end = t1

    # trailing gap
    if prev_end < total_dur - 0.01:
        p = work_dir / f"s{rank}_lz_trail.png"
        make_frame(None, current_imgs).save(p)
        trail_dur = max(total_dur - prev_end, MIN_SUBTITLE_FRAME_DURATION)
        concat_lines.append(f"file '{p.resolve()}'\nduration {trail_dur:.3f}")

    # ffmpeg concat sentinel (last entry repeated without duration)
    if concat_lines:
        concat_lines.append(concat_lines[-1].split("\n")[0])

    concat_path = work_dir / f"s{rank}_lz_concat.txt"
    concat_path.write_text("\n".join(concat_lines), encoding="utf-8")

    sub_video = work_dir / f"s{rank}_sub.mp4"
    run_ff([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-fps_mode", "vfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-movflags", "+faststart",
        str(sub_video),
    ])
    return sub_video, overlay_y


def fmt_srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    ms = int((s % 1) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def chunk_subtitle_text(text: str, max_chars: int = 14, max_lines: int = 3) -> list[str]:
    lines = wrap_text(sanitize_render_text(text), max_chars=max_chars)
    if not lines:
        return []
    return [" ".join(lines[i:i + max_lines]).strip() for i in range(0, len(lines), max_lines)]


def split_duration_by_text(text_chunks: list[str], start: float, end: float) -> list[tuple[float, float, str]]:
    if not text_chunks:
        return []
    total_chars = sum(max(len(chunk.replace(" ", "")), 1) for chunk in text_chunks)
    current = start
    entries: list[tuple[float, float, str]] = []
    for index, chunk in enumerate(text_chunks):
        remaining = end - current
        remaining_chunks = len(text_chunks) - index
        if remaining <= 0:
            break
        if index == len(text_chunks) - 1:
            chunk_end = end
        else:
            weight = max(len(chunk.replace(" ", "")), 1) / total_chars
            chunk_end = current + max(remaining * weight, MIN_SUBTITLE_FRAME_DURATION)
            max_end = end - MIN_SUBTITLE_FRAME_DURATION * (remaining_chunks - 1)
            chunk_end = min(chunk_end, max_end)
        entries.append((current, chunk_end, chunk))
        current = chunk_end
    return entries


def _clean_word(word: str) -> str:
    return sanitize_render_text(word).strip()


def merge_words_to_japanese_tokens(words: list[dict]) -> list[dict]:
    cleaned_words = []
    for word in words:
        cleaned = _clean_word(str(word.get("word", "")))
        if not cleaned:
            continue
        cleaned_words.append({
            "start": float(word["start"]),
            "end": float(word["end"]),
            "word": cleaned,
        })

    if not cleaned_words:
        return []

    merged_text = "".join(item["word"] for item in cleaned_words)
    surfaces = [token.surface for token in JANOME_TOKENIZER.tokenize(merged_text) if token.surface]
    if not surfaces:
        return cleaned_words

    # Build a char-position → Whisper word index map.
    # This handles cases where a Whisper word spans multiple Janome morphemes
    # (e.g. Whisper outputs "モデル末" while Janome splits it into "モデル" + "末期").
    char_to_wi: list[int] = []
    for wi, w in enumerate(cleaned_words):
        for _ in w["word"]:
            char_to_wi.append(wi)
    if len(char_to_wi) != len(merged_text):
        return cleaned_words  # shouldn't happen, safety fallback

    merged_tokens: list[dict] = []
    pos = 0
    for surface in surfaces:
        end_pos = pos + len(surface)
        if end_pos > len(merged_text):
            return cleaned_words  # Janome surfaces don't cover the full text, fallback
        merged_tokens.append({
            "start": cleaned_words[char_to_wi[pos]]["start"],
            "end": cleaned_words[char_to_wi[end_pos - 1]]["end"],
            "word": surface,
        })
        pos = end_pos

    if pos != len(merged_text):
        return cleaned_words

    return merged_tokens


def build_entries_from_word_timestamps(words: list[dict]) -> list[tuple[float, float, str]]:
    if not words:
        return []

    words = merge_words_to_japanese_tokens(words)

    entries: list[tuple[float, float, str]] = []
    current_text: list[str] = []
    cue_start: float | None = None
    cue_end: float | None = None
    pending_flush = False  # deferred hard-limit: flush before the NEXT token

    def flush() -> None:
        nonlocal current_text, cue_start, cue_end
        if cue_start is None or cue_end is None:
            return
        text = "".join(current_text).strip()
        if text and cue_end - cue_start >= MIN_SUBTITLE_FRAME_DURATION:
            entries.append((cue_start, cue_end, text))
        current_text = []
        cue_start = None
        cue_end = None

    def current_line_count() -> int:
        if not current_text:
            return 0
        lines = wrap_text("".join(current_text), max_chars=14)
        return max(1, min(len(lines), 3))

    for w in sorted(words, key=lambda x: float(x["start"])):
        token = _clean_word(str(w.get("word", "")))
        if not token:
            continue

        w_start = float(w["start"])
        w_end = float(w["end"])

        # A token that starts with a kinsoku-forbidden character (ー, small kana, …)
        # must NEVER begin a new subtitle entry — absorb it into the current one.
        can_start_entry = not (token and token[0] in _SUBTITLE_NO_START)

        # Check whether flushing HERE would break a natural Japanese unit:
        #   - っ/ッ at end of current cue must be followed by its consonant
        #   - a digit immediately before a counter/unit word (万, 円, 個, …)
        last_char = "".join(current_text)[-1] if current_text else ""
        can_break_here = not (
            last_char in _CUE_NO_END
            or (last_char.isdigit() and token and token[0] in _COUNTER_CHARS)
        )

        if cue_start is None:
            cue_start = w_start
        elif cue_end is not None and w_start - cue_end > SUBTITLE_WORD_GAP_SEC:
            # Actual silence between words (previous end → current start).
            # Skip the flush when the incoming token is a continuation char
            # (e.g. "ーブル" must not start a new entry after "ケ").
            current_duration = cue_end - cue_start
            if current_duration >= SUBTITLE_MIN_CUE_DURATION and can_start_entry and can_break_here:
                flush()
                cue_start = w_start
                pending_flush = False
        elif pending_flush and can_start_entry and can_break_here:
            # Deferred hard-limit: flush BEFORE this token so the cut falls
            # between complete morphemes, never mid-word.
            flush()
            cue_start = w_start
            pending_flush = False

        current_text.append(token)
        cue_end = w_end

        joined = "".join(current_text)
        cue_duration = cue_end - cue_start
        cue_chars = len(joined.replace(" ", ""))
        line_count = current_line_count()

        reached_hard_limit = cue_duration >= SUBTITLE_MAX_CUE_DURATION or cue_chars >= SUBTITLE_MAX_CUE_CHARS
        punctuation_break = (
            token.endswith(("。", "！", "？", "!", "?"))
            and cue_duration >= SUBTITLE_MIN_CUE_DURATION
            and cue_chars >= SUBTITLE_MIN_CUE_CHARS
        )
        # Only break when text has grown to fill 3 lines (≥29 chars at 14/line).
        preferred_break = (
            cue_duration >= SUBTITLE_PREFERRED_CUE_DURATION
            and line_count >= 3
        )

        if punctuation_break or preferred_break:
            flush()
            pending_flush = False
        elif reached_hard_limit:
            # Don't flush immediately — defer to start of next token so the
            # break always lands on a morpheme boundary.
            pending_flush = True

    flush()
    return entries


def build_story_srt(
    story_segments: list[dict],
    transcript_segs: list[dict],
    clip_durations: list[float],
) -> str:
    entries: list[tuple[float, float, str]] = []
    output_time = 0.0
    for i, seg in enumerate(story_segments):
        seg_start = seg["start"]
        # Use actual clip duration so subtitles cover content extended by find_natural_end
        seg_duration = clip_durations[i]
        seg_end_actual = seg_start + seg_duration
        seg_entries: list[tuple[float, float, str]] = []

        words_for_segment: list[dict] = []
        for ts in transcript_segs:
            if ts["end"] <= seg_start or ts["start"] >= seg_end_actual:
                continue
            for word in ts.get("words") or []:
                w_start = float(word.get("start", ts["start"]))
                w_end = float(word.get("end", ts["end"]))
                if w_end <= seg_start or w_start >= seg_end_actual:
                    continue
                local_start = output_time + max(w_start - seg_start, 0.0)
                local_end = output_time + min(w_end - seg_start, seg_duration)
                if local_end > local_start:
                    words_for_segment.append({
                        "start": local_start,
                        "end": local_end,
                        "word": word.get("word", ""),
                    })

        if words_for_segment:
            seg_entries.extend(build_entries_from_word_timestamps(words_for_segment))
        else:
            for ts in transcript_segs:
                if ts["end"] <= seg_start or ts["start"] >= seg_end_actual:
                    continue
                t0 = output_time + max(ts["start"] - seg_start, 0.0)
                t1 = output_time + min(ts["end"] - seg_start, seg_duration)
                if t1 > t0:
                    seg_entries.extend(split_duration_by_text(chunk_subtitle_text(ts["text"]), t0, t1))

        entries.extend(seg_entries)
        if i < len(story_segments) - 1:
            next_tr = story_segments[i + 1].get("transition_in", "dissolve")
            _, t = XFADE_TYPE.get(next_tr, ("dissolve", TRANSITION_DURATION))
            # Match join_segments: also constrain by next clip's duration
            t = min(t, clip_durations[i] * 0.9, clip_durations[i + 1] * 0.9)
            output_time += clip_durations[i] - t
    lines: list[str] = []
    cleaned_entries: list[tuple[float, float, str]] = []
    for t0, t1, text in entries:
        if t1 - t0 < MIN_SUBTITLE_FRAME_DURATION:
            continue
        if cleaned_entries and cleaned_entries[-1][2] == text and t0 <= cleaned_entries[-1][1] + 0.01:
            prev_t0, _, prev_text = cleaned_entries[-1]
            cleaned_entries[-1] = (prev_t0, t1, prev_text)
            continue
        cleaned_entries.append((t0, t1, text))

    for idx, (t0, t1, text) in enumerate(cleaned_entries, 1):
        lines.append(f"{idx}\n{fmt_srt_time(t0)} --> {fmt_srt_time(t1)}\n{text}\n")
    return "\n".join(lines)


def cache_token(value: str | None) -> str:
    if not value:
        return "auto"
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def add_overlays(
    joined_path: Path,
    story: dict,
    transcript_segs: list[dict],
    clip_durations: list[float],
    work_dir: Path,
    out_path: Path,
    bg_color: str,
    add_captions: bool,
    input_images: list[Path],
    video_title: str = "",
) -> None:
    """
    Composite overlays onto joined video using ffmpeg's overlay filter:
      [0] main video
      [1] upper PNG  (title + hook)  → overlay at y=0
      [2] subtitle video             → overlay at y=LOWER_Y  (if add_captions)
    """
    rank = story["rank"]

    upper_png = create_upper_text_image(story, bg_color, work_dir, rank, video_title)

    inputs = ["-i", str(joined_path), "-i", str(upper_png)]

    if add_captions:
        srt_content = build_story_srt(story["segments"], transcript_segs, clip_durations)
        entries = parse_srt(srt_content)
        total_dur = get_clip_duration(joined_path)
        sub_video, sub_y = create_subtitle_video(
            entries, total_dur, bg_color, work_dir, rank, input_images
        )
        inputs += ["-i", str(sub_video)]
        filter_complex = (
            f"[0:v][1:v]overlay=0:{UPPER_PNG_Y - TEXT_GAP}[v1];"
            f"[v1][2:v]overlay=0:{sub_y}[vout]"
        )
        map_v = "[vout]"
    else:
        filter_complex = f"[0:v][1:v]overlay=0:{UPPER_PNG_Y - TEXT_GAP}[vout]"
        map_v = "[vout]"

    run_ff(
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", filter_complex,
           "-map", map_v, "-map", "0:a",
           "-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-c:a", "copy", "-movflags", "+faststart",
           str(out_path)]
    )


# ──────────────────────────────────────────
# Render one story
# ──────────────────────────────────────────
def find_natural_end(
    end_time: float,
    transcript_segs: list[dict],
    window: float = 12.0,
) -> float:
    """Snap end_time to the nearest Whisper segment boundary within ±window seconds.
    Prefers the next sentence end *after* end_time (finish the current thought),
    then falls back to the latest boundary *before* end_time.
    """
    after  = [s["end"] for s in transcript_segs if 0 < s["end"] - end_time <= window]
    before = [s["end"] for s in transcript_segs if 0 <= end_time - s["end"] <= window]
    if after:
        return min(after)   # earliest end after → finishes current sentence
    if before:
        return max(before)  # latest end before → last clean break point
    return end_time         # nothing nearby, keep original


def render_story(
    story: dict,
    video_path: Path,
    transcript_segs: list[dict],
    out_dir: Path,
    work_dir: Path,
    bg_color: str,
    add_captions: bool,
    input_images: list[Path],
    max_total_duration: float,
    video_title: str = "",
) -> Path:
    rank = story["rank"]
    segs = story["segments"]
    transitions = [s.get("transition_in", "dissolve") for s in segs[1:]]

    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in story["title"]
    )[:50].strip()
    out_path = out_dir / f"{rank:02d}_{safe_title}.mp4"

    segment_times: list[tuple[float, float]] = []
    for i, seg in enumerate(segs):
        start = float(seg["start"])
        end = float(seg["end"])
        if i == len(segs) - 1:
            end = min(find_natural_end(end, transcript_segs, window=DURATION_TOLERANCE_SEC), end + DURATION_TOLERANCE_SEC)
        segment_times.append((start, end))

    transitions = [s.get("transition_in", "dissolve") for s in segs[1:]]
    est = estimate_joined_duration([end - start for start, end in segment_times], transitions)
    if est > max_total_duration and segment_times:
        overflow = est - max_total_duration
        last_start, last_end = segment_times[-1]
        min_end = last_start + MIN_SEGMENT_DURATION
        segment_times[-1] = (last_start, max(min_end, last_end - overflow))

    seg_paths: list[Path] = []
    for i, (start, end) in enumerate(segment_times):
        seg_path = work_dir / f"s{rank}_seg{i}.mp4"
        cut_segment(video_path, start, end, seg_path, bg_color)
        seg_paths.append(seg_path)

    durations = [get_clip_duration(p) for p in seg_paths]

    joined_path = work_dir / f"s{rank}_joined.mp4"
    join_segments(seg_paths, transitions, joined_path)

    add_overlays(
        joined_path, story, transcript_segs, durations,
        work_dir, out_path, bg_color, add_captions, input_images, video_title,
    )
    return out_path


# ──────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────
def cleanup_render_dir() -> None:
    """Delete temp/render/ (segment clips, PNGs, concat files).
    Keeps: downloaded video and transcript/story JSON caches in temp/."""
    render_dir = TEMP_DIR / "render"
    if render_dir.exists():
        shutil.rmtree(render_dir)
        print(f"   Removed {render_dir}/")
    else:
        print(f"   {render_dir}/ already clean")


# ──────────────────────────────────────────
# Render all stories
# ──────────────────────────────────────────
def render_all_stories(
    video_path: Path,
    stories: list[dict],
    transcript: dict,
    out_dir: Path,
    max_duration: int,
    bg_color: str = "white",
    add_captions: bool = True,
    input_images: list[Path] = [],
    video_title: str = "",
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = TEMP_DIR / "render"
    work_dir.mkdir(parents=True, exist_ok=True)

    info = get_video_info(video_path)
    print(f"\n[4/5] Rendering {len(stories)} stories…")
    print(f"   Source:  {info['width']}×{info['height']}, {info['duration']:.1f}s")
    print(f"   Output:  {TARGET_W}×{TARGET_H}  bg={bg_color}")
    print(f"   Zones:   [{TOP_ZONE_H}px title+hook] [{INNER_H}px video] [{BOTTOM_ZONE_H}px subs]")
    if video_title:
        print(f"   Title:   {video_title}")
    if input_images:
        print(f"   Images:  {len(input_images)} files in input/ → shown around subtitles")

    output_paths: list[Path] = []
    for story in stories:
        rank = story["rank"]
        segs = story["segments"]
        total = sum(s["end"] - s["start"] for s in segs)
        trs = [s.get("transition_in", "dissolve") for s in segs[1:]]
        print(
            f"   #{rank:2d} ({len(segs)}segs ~{total:.0f}s tr={trs}) "
            f"{story['title'][:40]}…",
            end=" ", flush=True,
        )
        try:
            p = render_story(
                story, video_path, transcript["segments"],
                out_dir, work_dir, bg_color, add_captions, input_images,
                max_total_duration=max_duration + DURATION_TOLERANCE_SEC,
                video_title=video_title,
            )
            size_mb = p.stat().st_size / 1024 / 1024
            print(f"→ {p.name} ({size_mb:.1f} MB)")
            output_paths.append(p)
        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr or ""
            err_lines = [ln for ln in stderr_text.splitlines() if ln.strip()]
            tail = "\n      ".join(err_lines[-6:]) if err_lines else "(no stderr)"
            print(f"FAILED\n      {tail}")

    return output_paths


# ──────────────────────────────────────────
# Main
# ──────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Turn a long YouTube video into story-based short videos."
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("-n", "--num-stories", type=int, default=DEFAULT_NUM_STORIES,
                        help=f"Number of short videos to generate (default: {DEFAULT_NUM_STORIES})")
    parser.add_argument("--duration", default="30-60",
                        choices=list(DURATION_PRESETS.keys()),
                        help="Target duration range per story in seconds (default: 30-60)")
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "base", "small", "medium", "large"])
    parser.add_argument("--whisper-language", default="ja",
                        help="Language hint for Whisper transcription (default: ja). "
                             "Use 'en' for English, or leave empty for auto-detect.")
    parser.add_argument("--no-proofread", action="store_true",
                        help="Skip Claude AI proofreading of the transcript")
    parser.add_argument("--bg-color", default="white", choices=["black", "white"],
                        help="Background color (default: white)")
    parser.add_argument("--no-captions", action="store_true",
                        help="Skip subtitle overlay (upper hook text is always shown)")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--skip-download", metavar="VIDEO_PATH")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    TEMP_DIR.mkdir(exist_ok=True)

    if args.skip_download:
        video_path = Path(args.skip_download)
        print(f"[1/5] Using local file: {video_path}")
        video_title = fetch_video_title(args.url)
        if video_title:
            print(f"   Title: {video_title}")
    else:
        video_path, video_title = download_video(args.url, TEMP_DIR)

    whisper_lang = args.whisper_language if args.whisper_language else None
    whisper_lang = args.whisper_language if args.whisper_language else None
    cache_suffix = f"v{TRANSCRIPT_CACHE_VERSION}_{args.whisper_model}_{cache_token(whisper_lang)}"
    cache = TEMP_DIR / f"{video_path.stem}_transcript_{cache_suffix}.json"
    if cache.exists():
        print(f"\n[2/5] Loading cached transcript…")
        transcript = json.loads(cache.read_text())
    else:
        transcript = transcribe_video(video_path, model_name=args.whisper_model, language=whisper_lang)
        cache.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    if not args.no_proofread:
        proofread_cache = TEMP_DIR / f"{video_path.stem}_transcript_proofread_{cache_suffix}.json"
        if proofread_cache.exists():
            print(f"\n[2/5] Loading cached proofread transcript…")
            transcript = json.loads(proofread_cache.read_text())
        else:
            print(f"\n[2/5] Proofreading transcript…")
            transcript = proofread_transcript(transcript)
            proofread_cache.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    info = get_video_info(video_path)

    # Cache key includes num_stories + duration range so different settings stay separate
    min_dur, max_dur = DURATION_PRESETS[args.duration]
    story_cache = TEMP_DIR / f"{video_path.stem}_stories_{args.num_stories}_{args.duration}.json"
    if story_cache.exists():
        print(f"\n[3/5] Loading cached stories ({args.num_stories} videos, {args.duration}s)…")
        stories = json.loads(story_cache.read_text())
        for s in stories:
            segs = s["segments"]
            total = sum(sg["end"] - sg["start"] for sg in segs)
            trs = [sg.get("transition_in", "-") for sg in segs[1:]]
            print(f"   #{s['rank']:2d} {s['title'][:45]:<45} {len(segs)}segs {total:.0f}s tr={trs}")
    else:
        stories = extract_stories(
            transcript, num_stories=args.num_stories, video_duration=info["duration"],
            min_dur=min_dur, max_dur=max_dur,
        )
        story_cache.write_text(json.dumps(stories, ensure_ascii=False, indent=2))

    normalized_count = enforce_story_duration_range(
        stories,
        min_total=min_dur,
        max_total=max_dur,
        video_duration=info["duration"],
        tolerance_sec=DURATION_TOLERANCE_SEC,
    )
    if normalized_count:
        print(
            f"\n[3/5] Adjusted {normalized_count} stories to match duration {args.duration}s (±{DURATION_TOLERANCE_SEC}s)"
        )
        for s in stories:
            segs = s["segments"]
            durs = [sg["end"] - sg["start"] for sg in segs]
            trs = [sg.get("transition_in", "dissolve") for sg in segs[1:]]
            est = estimate_joined_duration(durs, trs)
            print(f"   #{s['rank']:2d} {len(segs)}segs ≈{est:.1f}s")

    input_images = scan_input_images()

    output_paths = render_all_stories(
        video_path, stories, transcript, out_dir,
        max_duration=max_dur,
        bg_color=args.bg_color,
        add_captions=not args.no_captions,
        input_images=input_images,
        video_title=video_title,
    )

    print(f"\n[5/5] Done! {len(output_paths)}/{len(stories)} stories → {out_dir.resolve()}")
    for p in output_paths:
        print(f"   {p.name}")

    print("\n[Cleanup] Removing intermediate render files…")
    cleanup_render_dir()
    print("   Kept: downloaded video + transcript/story JSON caches in temp/")


if __name__ == "__main__":
    main()
