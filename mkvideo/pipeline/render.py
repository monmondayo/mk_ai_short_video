"""Step 4-5: Video segment cutting, joining, subtitle generation, and final rendering."""

import json
import random
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import constants as _const
from .constants import (
    BOTTOM_ZONE_H,
    DURATION_TOLERANCE_SEC,
    IMG_CHANGE_INTERVAL,
    IMG_GAP,
    INNER_H,
    LOWER_FG,
    LOWER_STROKE,
    LOWER_Y,
    MIN_SEGMENT_DURATION,
    MIN_SUBTITLE_FRAME_DURATION,
    STROKE_W,
    SUBTITLE_MAX_CUE_CHARS,
    SUBTITLE_MAX_CUE_DURATION,
    SUBTITLE_MIN_CUE_CHARS,
    SUBTITLE_MIN_CUE_DURATION,
    SUBTITLE_PREFERRED_CUE_DURATION,
    SUBTITLE_WORD_GAP_SEC,
    TARGET_H,
    TARGET_W,
    TEXT_GAP,
    TEXT_PNG_H,
    TOP_ZONE_H,
    TRANSITION_DURATION,
    UPPER_FG,
    UPPER_PNG_Y,
    UPPER_STROKE,
    XFADE_TYPE,
    _COUNTER_CHARS,
    _CUE_NO_END,
    _IMAGE_EXTS,
    _SUBTITLE_NO_START,
    estimate_joined_duration,
)

# NOTE: TEMP_DIR and INPUT_DIR are NOT imported by value — they're mutable
# runtime paths that modal_app.py rebinds to a per-invocation tempdir. If we
# `from .constants import TEMP_DIR`, the name is frozen to constants.py's
# default (Path("temp")) at import time and Modal's rebind has no effect.
# Access them through the module object instead so every read sees the
# current value.
from .fonts import bg_rgb, find_system_font, load_font, sanitize_render_text
from .text_layout import JANOME_TOKENIZER, wrap_and_fit, wrap_text


def parse_hex_color(value: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Parse #RRGGBB / #RGB color strings, falling back to ``default`` on error."""
    if not value:
        return default

    raw = value.strip()
    if raw.startswith("#"):
        raw = raw[1:]

    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)

    if len(raw) != 6:
        return default

    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default


# ──────────────────────────────────────────
# ffmpeg helpers
# ──────────────────────────────────────────
def run_ff(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )


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


# ──────────────────────────────────────────
# Input images
# ──────────────────────────────────────────
def scan_input_images() -> list[Path]:
    """Return sorted list of image files found in input/."""
    if not _const.INPUT_DIR.exists():
        return []
    return sorted(p for p in _const.INPUT_DIR.iterdir() if p.suffix.lower() in _IMAGE_EXTS)


def fit_image_in_box(img_path: Path, box_w: int, box_h: int, bg: tuple) -> Image.Image:
    """Load an image and resize/pad it to exactly box_w x box_h."""
    try:
        src = Image.open(img_path).convert("RGB")
        src.thumbnail((box_w, box_h), Image.LANCZOS)
        canvas = Image.new("RGB", (box_w, box_h), bg)
        canvas.paste(src, ((box_w - src.width) // 2, (box_h - src.height) // 2))
        return canvas
    except Exception:
        return Image.new("RGB", (box_w, box_h), bg)


# ──────────────────────────────────────────
# Segment cutting and joining
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


# ──────────────────────────────────────────
# Text overlay images
# ──────────────────────────────────────────
def create_upper_text_image(
    story: dict,
    bg_color: str,
    work_dir: Path,
    rank: int,
    video_title: str = "",
    upper_text_color: tuple[int, int, int] = UPPER_FG,
    upper_edge_color: tuple[int, int, int] = UPPER_STROKE,
) -> Path:
    VT_SIZE    = 20
    VT_GAP     = 4
    TITLE_SIZE = 28
    HOOK_SIZE  = 64
    TITLE_GAP  = 6
    BOTTOM_PAD = 4

    font_path = find_system_font()

    hook_lines, hook_font, hook_lh = wrap_and_fit(
        sanitize_render_text(story.get("hook", story["title"])), font_path, HOOK_SIZE, max_lines=2)
    hook_block_h = hook_lh * len(hook_lines)

    title_lines, title_font, title_lh = wrap_and_fit(
        sanitize_render_text(story["title"]), font_path, TITLE_SIZE, max_lines=2, min_size=16)
    title_block_h = title_lh * len(title_lines)

    has_vt = bool(video_title.strip())
    if has_vt:
        vt_lines, vt_font, vt_lh = wrap_and_fit(
            sanitize_render_text(video_title), font_path, VT_SIZE, max_lines=2, min_size=12)
        vt_block = vt_lh * len(vt_lines) + VT_GAP
    else:
        vt_lines, vt_lh = [], 0
        vt_block = 0

    total_h = vt_block + title_block_h + TITLE_GAP + hook_block_h
    y = max(TEXT_PNG_H - BOTTOM_PAD - total_h, 4)

    img  = Image.new("RGB", (TARGET_W, TEXT_PNG_H), bg_rgb(bg_color))
    draw = ImageDraw.Draw(img)

    if has_vt:
        for vt_line in vt_lines:
            draw.text((TARGET_W // 2, y), vt_line,
                      font=vt_font, fill=upper_text_color, anchor="mt",
                      stroke_width=max(STROKE_W - 4, 2), stroke_fill=upper_edge_color)
            y += vt_lh
        y += VT_GAP

    for title_line in title_lines:
        draw.text((TARGET_W // 2, y), title_line,
                  font=title_font, fill=upper_text_color, anchor="mt",
                  stroke_width=STROKE_W, stroke_fill=upper_edge_color)
        y += title_lh
    y += TITLE_GAP

    for line in hook_lines:
        draw.text((TARGET_W // 2, y), line, font=hook_font, fill=upper_text_color, anchor="mt",
                  stroke_width=STROKE_W, stroke_fill=upper_edge_color)
        y += hook_lh

    path = work_dir / f"s{rank}_upper.png"
    img.save(path)
    return path


def _render_subtitle_img(
    text: str,
    bg_color: str,
    font_path: str,
    subtitle_text_color: tuple[int, int, int] = LOWER_FG,
    subtitle_edge_color: tuple[int, int, int] = LOWER_STROKE,
) -> Image.Image:
    SUB_START_SIZE = 72
    TOP_PAD        = 8
    img  = Image.new("RGB", (TARGET_W, TEXT_PNG_H), bg_rgb(bg_color))
    draw = ImageDraw.Draw(img)
    clean = sanitize_render_text(text)
    lines, font, line_h = wrap_and_fit(clean, font_path, SUB_START_SIZE, max_lines=3, min_size=24)
    y = TOP_PAD
    for line in lines:
        draw.text((TARGET_W // 2, y), line, font=font, fill=subtitle_text_color, anchor="mt",
                  stroke_width=STROKE_W, stroke_fill=subtitle_edge_color)
        y += line_h
    return img


def create_subtitle_png(
    text: str,
    bg_color: str,
    font_path: str,
    work_dir: Path,
    name: str,
    subtitle_text_color: tuple[int, int, int] = LOWER_FG,
    subtitle_edge_color: tuple[int, int, int] = LOWER_STROKE,
) -> Path:
    path = work_dir / name
    _render_subtitle_img(
        text,
        bg_color,
        font_path,
        subtitle_text_color=subtitle_text_color,
        subtitle_edge_color=subtitle_edge_color,
    ).save(path)
    return path


# ──────────────────────────────────────────
# SRT parsing and generation
# ──────────────────────────────────────────
def parse_srt(srt_content: str) -> list[tuple[float, float, str]]:
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

    char_to_wi: list[int] = []
    for wi, w in enumerate(cleaned_words):
        for _ in w["word"]:
            char_to_wi.append(wi)
    if len(char_to_wi) != len(merged_text):
        return cleaned_words

    merged_tokens: list[dict] = []
    pos = 0
    for surface in surfaces:
        end_pos = pos + len(surface)
        if end_pos > len(merged_text):
            return cleaned_words
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
    pending_flush = False

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

        can_start_entry = not (token and token[0] in _SUBTITLE_NO_START)

        last_char = "".join(current_text)[-1] if current_text else ""
        can_break_here = not (
            last_char in _CUE_NO_END
            or (last_char.isdigit() and token and token[0] in _COUNTER_CHARS)
        )

        if cue_start is None:
            cue_start = w_start
        elif cue_end is not None and w_start - cue_end > SUBTITLE_WORD_GAP_SEC:
            current_duration = cue_end - cue_start
            if current_duration >= SUBTITLE_MIN_CUE_DURATION and can_start_entry and can_break_here:
                flush()
                cue_start = w_start
                pending_flush = False
        elif pending_flush and can_start_entry and can_break_here:
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
        preferred_break = (
            cue_duration >= SUBTITLE_PREFERRED_CUE_DURATION
            and line_count >= 3
        )

        if punctuation_break or preferred_break:
            flush()
            pending_flush = False
        elif reached_hard_limit:
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


# ──────────────────────────────────────────
# Subtitle video creation
# ──────────────────────────────────────────
def create_subtitle_video(
    entries: list[tuple[float, float, str]],
    total_dur: float,
    bg_color: str,
    work_dir: Path,
    rank: int,
    input_images: list[Path],
    subtitle_text_color: tuple[int, int, int] = LOWER_FG,
    subtitle_edge_color: tuple[int, int, int] = LOWER_STROKE,
) -> tuple[Path, int]:
    font_path  = find_system_font()
    use_imgs   = bool(input_images)
    frame_h    = BOTTOM_ZONE_H if use_imgs else TEXT_PNG_H
    overlay_y  = LOWER_Y       if use_imgs else LOWER_Y + TEXT_GAP
    bg         = bg_rgb(bg_color)

    img_row_h = (BOTTOM_ZONE_H - TEXT_PNG_H - 2 * IMG_GAP) // 2
    img_w     = TARGET_W // 2

    rng = random.Random(rank)

    def pick4() -> list[Path]:
        if len(input_images) >= 4:
            return rng.sample(input_images, 4)
        return rng.choices(input_images, k=4)

    def make_frame(text: str | None, imgs: list[Path]) -> Image.Image:
        if not use_imgs:
            if text is None:
                return Image.new("RGB", (TARGET_W, frame_h), bg)
            return _render_subtitle_img(
                text,
                bg_color,
                font_path,
                subtitle_text_color=subtitle_text_color,
                subtitle_edge_color=subtitle_edge_color,
            )

        canvas = Image.new("RGB", (TARGET_W, BOTTOM_ZONE_H), bg)
        for j in range(2):
            canvas.paste(fit_image_in_box(imgs[j], img_w, img_row_h, bg), (j * img_w, 0))
        sub_y = img_row_h + IMG_GAP
        sub_img = _render_subtitle_img(
            text,
            bg_color,
            font_path,
            subtitle_text_color=subtitle_text_color,
            subtitle_edge_color=subtitle_edge_color,
        ) if text else \
                  Image.new("RGB", (TARGET_W, TEXT_PNG_H), bg)
        canvas.paste(sub_img, (0, sub_y))
        bot_y = sub_y + TEXT_PNG_H + IMG_GAP
        for j in range(2):
            canvas.paste(fit_image_in_box(imgs[j + 2], img_w, img_row_h, bg),
                         (j * img_w, bot_y))
        return canvas

    current_imgs: list[Path] = pick4() if use_imgs else []
    concat_lines: list[str]  = []
    prev_end = 0.0

    for i, (t0, t1, text) in enumerate(entries):
        if t0 > prev_end + 0.01:
            p = work_dir / f"s{rank}_lz_b{i}.png"
            make_frame(None, current_imgs).save(p)
            blank_dur = max(t0 - prev_end, MIN_SUBTITLE_FRAME_DURATION)
            concat_lines.append(f"file '{p.resolve()}'\nduration {blank_dur:.3f}")

        if use_imgs and i % IMG_CHANGE_INTERVAL == 0:
            current_imgs = pick4()

        p = work_dir / f"s{rank}_lz_{i}.png"
        make_frame(text, current_imgs).save(p)
        text_dur = max(t1 - t0, MIN_SUBTITLE_FRAME_DURATION)
        concat_lines.append(f"file '{p.resolve()}'\nduration {text_dur:.3f}")
        prev_end = t1

    if prev_end < total_dur - 0.01:
        p = work_dir / f"s{rank}_lz_trail.png"
        make_frame(None, current_imgs).save(p)
        trail_dur = max(total_dur - prev_end, MIN_SUBTITLE_FRAME_DURATION)
        concat_lines.append(f"file '{p.resolve()}'\nduration {trail_dur:.3f}")

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


# ──────────────────────────────────────────
# Final overlay composition
# ──────────────────────────────────────────
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
    srt_content: str | None = None,
    upper_text_color: tuple[int, int, int] = UPPER_FG,
    upper_edge_color: tuple[int, int, int] = UPPER_STROKE,
    subtitle_text_color: tuple[int, int, int] = LOWER_FG,
    subtitle_edge_color: tuple[int, int, int] = LOWER_STROKE,
) -> None:
    rank = story["rank"]

    upper_png = create_upper_text_image(
        story,
        bg_color,
        work_dir,
        rank,
        video_title,
        upper_text_color=upper_text_color,
        upper_edge_color=upper_edge_color,
    )

    inputs = ["-i", str(joined_path), "-i", str(upper_png)]

    if add_captions:
        if srt_content is None:
            srt_content = build_story_srt(story["segments"], transcript_segs, clip_durations)
        entries = parse_srt(srt_content)
        total_dur = get_clip_duration(joined_path)
        sub_video, sub_y = create_subtitle_video(
            entries,
            total_dur,
            bg_color,
            work_dir,
            rank,
            input_images,
            subtitle_text_color=subtitle_text_color,
            subtitle_edge_color=subtitle_edge_color,
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
# Render one story end-to-end
# ──────────────────────────────────────────
def find_natural_end(
    end_time: float,
    transcript_segs: list[dict],
    window: float = 12.0,
) -> float:
    after  = [s["end"] for s in transcript_segs if 0 < s["end"] - end_time <= window]
    before = [s["end"] for s in transcript_segs if 0 <= end_time - s["end"] <= window]
    if after:
        return min(after)
    if before:
        return max(before)
    return end_time


def _prepare_story(
    story: dict,
    video_path: Path,
    transcript_segs: list[dict],
    work_dir: Path,
    bg_color: str,
    add_captions: bool,
    max_total_duration: float,
) -> tuple[Path, list[float], str]:
    """Cut segments, join, and generate SRT. Returns (joined_path, durations, srt_content)."""
    rank = story["rank"]
    segs = story["segments"]

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

    srt_content = ""
    if add_captions:
        srt_content = build_story_srt(story["segments"], transcript_segs, durations)
        srt_path = _const.TEMP_DIR / f"{video_path.stem}_s{rank}.srt"
        # Parent directory might not exist if caller rebound TEMP_DIR to a
        # fresh tempdir without pre-creating it — mkdir guards against that.
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(srt_content, encoding="utf-8")

    return joined_path, durations, srt_content


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
    upper_text_color: tuple[int, int, int] = UPPER_FG,
    upper_edge_color: tuple[int, int, int] = UPPER_STROKE,
    subtitle_text_color: tuple[int, int, int] = LOWER_FG,
    subtitle_edge_color: tuple[int, int, int] = LOWER_STROKE,
) -> Path:
    rank = story["rank"]
    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in story["title"]
    )[:50].strip()
    out_path = out_dir / f"{rank:02d}_{safe_title}.mp4"

    joined_path, durations, srt_content = _prepare_story(
        story, video_path, transcript_segs, work_dir, bg_color, add_captions, max_total_duration
    )
    add_overlays(
        joined_path, story, transcript_segs, durations,
        work_dir, out_path, bg_color, add_captions, input_images, video_title,
        srt_content=srt_content or None,
        upper_text_color=upper_text_color,
        upper_edge_color=upper_edge_color,
        subtitle_text_color=subtitle_text_color,
        subtitle_edge_color=subtitle_edge_color,
    )
    return out_path


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
    review_subtitles: bool = False,
    upper_text_color: tuple[int, int, int] = UPPER_FG,
    upper_edge_color: tuple[int, int, int] = UPPER_STROKE,
    subtitle_text_color: tuple[int, int, int] = LOWER_FG,
    subtitle_edge_color: tuple[int, int, int] = LOWER_STROKE,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = _const.TEMP_DIR / "render"
    work_dir.mkdir(parents=True, exist_ok=True)

    info = get_video_info(video_path)
    print(f"\n[4/5] Rendering {len(stories)} stories...")
    print(f"   Source:  {info['width']}x{info['height']}, {info['duration']:.1f}s")
    print(f"   Output:  {TARGET_W}x{TARGET_H}  bg={bg_color}")
    print(f"   Zones:   [{TOP_ZONE_H}px title+hook] [{INNER_H}px video] [{BOTTOM_ZONE_H}px subs]")
    if video_title:
        print(f"   Title:   {video_title}")
    if input_images:
        print(f"   Images:  {len(input_images)} files in input/ -> shown around subtitles")

    transcript_segs = transcript["segments"]
    max_total = max_duration + DURATION_TOLERANCE_SEC

    def _stderr_tail(e: subprocess.CalledProcessError) -> str:
        lines = [ln for ln in (e.stderr or "").splitlines() if ln.strip()]
        return "\n      ".join(lines[-6:]) if lines else "(no stderr)"

    def _safe_title(story: dict) -> str:
        return "".join(c if c.isalnum() or c in " -_" else "_" for c in story["title"])[:50].strip()

    output_paths: list[Path] = []

    if review_subtitles and add_captions:
        # ── Phase 1: cut + join + generate SRT for every story ──────────────
        print("   [Phase 1/2] セグメントカット・字幕生成中...")
        prepared: dict[int, tuple[Path, Path, list[float]]] = {}  # rank -> (out_path, joined_path, durations)
        for story in stories:
            rank = story["rank"]
            segs = story["segments"]
            total = sum(s["end"] - s["start"] for s in segs)
            trs = [s.get("transition_in", "dissolve") for s in segs[1:]]
            print(f"   #{rank:2d} ({len(segs)}segs ~{total:.0f}s tr={trs}) {story['title'][:40]}...", end=" ", flush=True)
            try:
                joined_path, durations, _ = _prepare_story(
                    story, video_path, transcript_segs, work_dir, bg_color, add_captions, max_total
                )
                out_path = out_dir / f"{rank:02d}_{_safe_title(story)}.mp4"
                prepared[rank] = (out_path, joined_path, durations)
                print("-> SRT生成済")
            except subprocess.CalledProcessError as e:
                print(f"FAILED\n      {_stderr_tail(e)}")

        # ── Pause for subtitle review ────────────────────────────────────────
        print(f"\n[Review] 字幕SRTファイルを確認・修正してください:")
        for story in stories:
            rank = story["rank"]
            if rank in prepared:
                srt_path = _const.TEMP_DIR / f"{video_path.stem}_s{rank}.srt"
                print(f"   #{rank:2d}: {srt_path.resolve()}")
        print("\n   編集が完了したら Enter を押してください...")
        input()

        # ── Phase 2: overlay with (possibly edited) SRT files ───────────────
        print("   [Phase 2/2] オーバーレイ合成中...")
        for story in stories:
            rank = story["rank"]
            if rank not in prepared:
                continue
            out_path, joined_path, durations = prepared[rank]
            print(f"   #{rank:2d} {story['title'][:50]}...", end=" ", flush=True)
            try:
                srt_path = _const.TEMP_DIR / f"{video_path.stem}_s{rank}.srt"
                srt_content = srt_path.read_text(encoding="utf-8") if srt_path.exists() else None
                add_overlays(
                    joined_path, story, transcript_segs, durations,
                    work_dir, out_path, bg_color, add_captions, input_images, video_title,
                    srt_content=srt_content,
                    upper_text_color=upper_text_color,
                    upper_edge_color=upper_edge_color,
                    subtitle_text_color=subtitle_text_color,
                    subtitle_edge_color=subtitle_edge_color,
                )
                size_mb = out_path.stat().st_size / 1024 / 1024
                print(f"-> {out_path.name} ({size_mb:.1f} MB)")
                output_paths.append(out_path)
            except subprocess.CalledProcessError as e:
                print(f"FAILED\n      {_stderr_tail(e)}")

    else:
        # ── Normal flow ──────────────────────────────────────────────────────
        for story in stories:
            rank = story["rank"]
            segs = story["segments"]
            total = sum(s["end"] - s["start"] for s in segs)
            trs = [s.get("transition_in", "dissolve") for s in segs[1:]]
            print(
                f"   #{rank:2d} ({len(segs)}segs ~{total:.0f}s tr={trs}) "
                f"{story['title'][:40]}...",
                end=" ", flush=True,
            )
            try:
                p = render_story(
                    story, video_path, transcript_segs,
                    out_dir, work_dir, bg_color, add_captions, input_images,
                    max_total_duration=max_total,
                    video_title=video_title,
                    upper_text_color=upper_text_color,
                    upper_edge_color=upper_edge_color,
                    subtitle_text_color=subtitle_text_color,
                    subtitle_edge_color=subtitle_edge_color,
                )
                size_mb = p.stat().st_size / 1024 / 1024
                print(f"-> {p.name} ({size_mb:.1f} MB)")
                output_paths.append(p)
            except subprocess.CalledProcessError as e:
                print(f"FAILED\n      {_stderr_tail(e)}")

    return output_paths


# ──────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────
def cleanup_render_dir() -> None:
    render_dir = _const.TEMP_DIR / "render"
    if render_dir.exists():
        shutil.rmtree(render_dir)
        print(f"   Removed {render_dir}/")
    else:
        print(f"   {render_dir}/ already clean")
