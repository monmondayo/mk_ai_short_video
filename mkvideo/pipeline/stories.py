"""Step 3: Extract stories from transcript via Claude API."""

import json
import os
from typing import Callable, Optional

import anthropic

from .constants import (
    DEFAULT_NUM_STORIES,
    MAX_SEGMENT_DURATION,
    MAX_STORY_DURATION,
    MIN_SEGMENT_DURATION,
    MIN_SEGMENT_GAP,
    MIN_STORY_DURATION,
    estimate_joined_duration,
)

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
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:
    """Extract stories from transcript via Claude.

    Args:
        progress_callback: Optional callback ``(current, total, detail)``
            invoked once per story as Claude streams them back, plus
            once at start and once at finish.
    """
    print(f"\n[3/5] Extracting {num_stories} stories with Claude...")
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
    min_seg = max(3, min_dur // 4)
    max_seg = min(max_dur // 2, MAX_SEGMENT_DURATION)
    system = STORY_SYSTEM.format(
        min_total=min_dur, max_total=max_dur,
        min_seg=min_seg, max_seg=max_seg,
    )

    def _notify(current: int, total: int, detail: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(current, total, detail)
        except Exception:
            # Progress reporting must never break extraction
            pass

    _notify(0, num_stories, "Analyzing transcript with Claude...")

    # Stream so we can report per-story progress as Claude generates them.
    # We detect new stories by counting '"rank"' occurrences in the
    # accumulated output — one per story object in the JSON schema.
    raw_parts: list[str] = []
    last_count = 0
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": (
            f"{duration_note}Create exactly {num_stories} stories.\n\nTRANSCRIPT:\n"
            + "\n".join(lines)
        )}],
    ) as stream:
        for delta in stream.text_stream:
            raw_parts.append(delta)
            # Cheap incremental check — count once when new chunk arrives.
            count = 0
            # Scan only the freshly appended text plus a small tail to catch
            # occurrences straddling the previous boundary.
            joined_tail = "".join(raw_parts[-4:])
            count = joined_tail.count('"rank"')
            # Re-derive absolute count from full buffer when needed
            total_count = ("".join(raw_parts)).count('"rank"')
            if total_count != last_count and total_count <= num_stories:
                last_count = total_count
                _notify(
                    total_count, num_stories,
                    f"Selected story {total_count}/{num_stories}",
                )
            _ = count  # silence unused

    raw = "".join(raw_parts).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    stories = json.loads(raw.strip())["stories"]
    _notify(len(stories), num_stories, "Normalizing story durations...")
    print(f"   Created {len(stories)} stories:")
    for s in stories:
        segs = s["segments"]
        total = sum(sg["end"] - sg["start"] for sg in segs)
        trs = [sg.get("transition_in", "-") for sg in segs[1:]]
        print(f"   #{s['rank']:2d} {s['title'][:45]:<45} {len(segs)}segs {total:.0f}s tr={trs}")
    return stories


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
