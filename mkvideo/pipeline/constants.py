"""Shared constants for the mkvideo pipeline."""

from pathlib import Path

# ──────────────────────────────────────────
# Layout constants (pixels)
# ──────────────────────────────────────────
TARGET_W, TARGET_H = 1080, 1920
# 16:9 source fills full width at 1080px → height = 1080×9/16 = 608px.
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
_SUBTITLE_NO_START = frozenset("ーぁぃぅぇぉっゃゅょァィゥェォッャュョ")
# Characters that cannot END a subtitle cue — the following token is inseparable.
_CUE_NO_END = frozenset("っッ")
# Counter/unit characters that must not be separated from a preceding digit.
_COUNTER_CHARS = frozenset("万億兆千百十円個本台枚人回度分秒年月日時間週冊匹頭羽杯着件点棟")

TEMP_DIR = Path("temp")

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

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
INPUT_DIR = Path("input")


def estimate_joined_duration(segment_durations: list[float], transitions: list[str]) -> float:
    """Estimate total duration after joining segments with xfade transitions."""
    if not segment_durations:
        return 0.0
    total = segment_durations[0]
    for i in range(1, len(segment_durations)):
        transition = transitions[i - 1] if i - 1 < len(transitions) else "dissolve"
        _, t = XFADE_TYPE.get(transition, ("dissolve", TRANSITION_DURATION))
        t = min(t, segment_durations[i - 1] * 0.9, segment_durations[i] * 0.9)
        total += segment_durations[i] - t
    return total
