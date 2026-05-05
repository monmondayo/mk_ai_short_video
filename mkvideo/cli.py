#!/usr/bin/env python3
"""
mkvideo CLI - YouTube Long Video -> Story-based Short Videos

Usage: python -m mkvideo.cli <youtube_url> [options]
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from .pipeline.constants import (
    DEFAULT_NUM_STORIES,
    DURATION_PRESETS,
    DURATION_TOLERANCE_SEC,
    TEMP_DIR,
    TRANSCRIPT_CACHE_VERSION,
    estimate_joined_duration,
)
from .pipeline.download import download_video, fetch_video_title
from .pipeline.render import (
    cleanup_render_dir,
    get_video_info,
    parse_hex_color,
    render_all_stories,
    scan_input_images,
)
from .pipeline.stories import enforce_story_duration_range, extract_stories
from .pipeline.transcribe import (
    export_review_txt,
    import_review_txt,
    proofread_transcript,
    transcribe_video,
)

# Load .env from project root so API keys are available
PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


def format_bytes(num_bytes: int) -> str:
    """Format bytes into a human-readable IEC string."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(num_bytes)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.2f} {units[idx]}"


def check_r2_usage(r2_prefix: str, free_limit_gb: float) -> int:
    """Print Cloudflare R2 usage and return process exit code."""
    try:
        from .storage.r2 import R2Storage
    except ModuleNotFoundError as exc:
        if exc.name == "boto3":
            print("[R2 Usage] Missing dependency: boto3")
            print("Install cloud dependencies first:")
            print("  pip install -r requirements-cloud.txt")
            return 1
        raise

    prefix = r2_prefix.strip("/")
    r2 = R2Storage(prefix=prefix)

    request_prefix = f"{prefix}/" if prefix else ""
    total_bytes = 0
    total_objects = 0
    continuation_token = None

    print("\n[R2 Usage] Collecting storage usage...")
    print(f"   Bucket: {r2.bucket_name}")
    print(f"   Prefix: {request_prefix or '(all objects)'}")

    while True:
        params = {"Bucket": r2.bucket_name, "MaxKeys": 1000}
        if request_prefix:
            params["Prefix"] = request_prefix
        if continuation_token:
            params["ContinuationToken"] = continuation_token

        response = r2.s3.list_objects_v2(**params)
        for obj in response.get("Contents", []):
            total_objects += 1
            total_bytes += int(obj.get("Size", 0))

        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    free_limit_bytes = int(free_limit_gb * 1_000_000_000)
    usage_percent = (total_bytes / free_limit_bytes * 100) if free_limit_bytes > 0 else 0.0
    remaining_bytes = free_limit_bytes - total_bytes
    is_within_limit = total_bytes <= free_limit_bytes

    print("\n[R2 Usage] Result")
    print(f"   Objects: {total_objects:,}")
    print(f"   Used:    {total_bytes:,} bytes ({format_bytes(total_bytes)})")
    print(f"   Limit:   {free_limit_bytes:,} bytes ({free_limit_gb:.2f} GB)")
    print(f"   Usage:   {usage_percent:.2f}%")

    if is_within_limit:
        print(f"   Status:  OK (remaining {remaining_bytes:,} bytes / {format_bytes(max(remaining_bytes, 0))})")
        return 0

    print(f"   Status:  OVER LIMIT by {abs(remaining_bytes):,} bytes / {format_bytes(abs(remaining_bytes))}")
    return 2


def cache_token(value: str | None) -> str:
    if not value:
        return "auto"
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def main():
    parser = argparse.ArgumentParser(
        description="Turn a long YouTube video into story-based short videos."
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL")
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
    parser.add_argument("--review-transcript", action="store_true",
                        help="Pause after transcription to manually edit the transcript")
    parser.add_argument("--review-subtitles", action="store_true",
                        help="Pause after subtitle generation to manually edit SRT files")
    parser.add_argument("--bg-color", default="white", choices=["black", "white"],
                        help="Background color (default: white)")
    parser.add_argument(
        "--upper-text-color",
        default="#000000",
        help="Upper text color (#RRGGBB or #RGB, default: #000000)",
    )
    parser.add_argument(
        "--upper-edge-color",
        default="#FFDC00",
        help="Upper text edge/stroke color (#RRGGBB or #RGB, default: #FFDC00)",
    )
    parser.add_argument(
        "--subtitle-text-color",
        default="#FFFFFF",
        help="Subtitle text color (#RRGGBB or #RGB, default: #FFFFFF)",
    )
    parser.add_argument(
        "--subtitle-edge-color",
        default="#FF1493",
        help="Subtitle edge/stroke color (#RRGGBB or #RGB, default: #FF1493)",
    )
    parser.add_argument("--no-captions", action="store_true",
                        help="Skip subtitle overlay (upper hook text is always shown)")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--skip-download", metavar="VIDEO_PATH")
    parser.add_argument("--check-r2-usage", action="store_true",
                        help="Check Cloudflare R2 bucket usage against a free-tier limit and exit")
    parser.add_argument("--r2-prefix", default="",
                        help="Optional R2 key prefix to limit usage scan (default: entire bucket)")
    parser.add_argument("--free-limit-gb", type=float, default=10.0,
                        help="Free-tier limit in GB for usage check (default: 10.0)")
    args = parser.parse_args()

    if args.check_r2_usage:
        return check_r2_usage(args.r2_prefix, args.free_limit_gb)

    if not args.url:
        parser.error("url is required unless --check-r2-usage is used")

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
    cache_suffix = f"v{TRANSCRIPT_CACHE_VERSION}_{args.whisper_model}_{cache_token(whisper_lang)}"
    cache = TEMP_DIR / f"{video_path.stem}_transcript_{cache_suffix}.json"
    if cache.exists():
        print(f"\n[2/5] Loading cached transcript...")
        transcript = json.loads(cache.read_text())
    else:
        transcript = transcribe_video(video_path, model_name=args.whisper_model, language=whisper_lang)
        cache.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    proofread_cache = TEMP_DIR / f"{video_path.stem}_transcript_proofread_{cache_suffix}.json"
    if not args.no_proofread:
        if proofread_cache.exists():
            print(f"\n[2/5] Loading cached proofread transcript...")
            transcript = json.loads(proofread_cache.read_text())
        else:
            print(f"\n[2/5] Proofreading transcript...")
            transcript = proofread_transcript(transcript)
            proofread_cache.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    if args.review_transcript:
        review_path = TEMP_DIR / f"{video_path.stem}_review.txt"
        export_review_txt(transcript, review_path)
        print(f"\n[Review] トランスクリプト確認・修正")
        print(f"   ファイル: {review_path.resolve()}")
        editor = os.environ.get("EDITOR")
        if editor:
            print(f"   エディタ ({editor}) で開きます...")
            subprocess.run([editor, str(review_path)])
        else:
            print("   ファイルを編集したら Enter を押してください")
            input()
        transcript = import_review_txt(review_path, transcript)
        save_to = proofread_cache if not args.no_proofread else cache
        save_to.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
        print("   修正済みトランスクリプトをキャッシュに保存しました")

    info = get_video_info(video_path)

    min_dur, max_dur = DURATION_PRESETS[args.duration]
    story_cache = TEMP_DIR / f"{video_path.stem}_stories_{args.num_stories}_{args.duration}.json"
    if story_cache.exists():
        print(f"\n[3/5] Loading cached stories ({args.num_stories} videos, {args.duration}s)...")
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
            f"\n[3/5] Adjusted {normalized_count} stories to match duration {args.duration}s (+/-{DURATION_TOLERANCE_SEC}s)"
        )
        for s in stories:
            segs = s["segments"]
            durs = [sg["end"] - sg["start"] for sg in segs]
            trs = [sg.get("transition_in", "dissolve") for sg in segs[1:]]
            est = estimate_joined_duration(durs, trs)
            print(f"   #{s['rank']:2d} {len(segs)}segs ~={est:.1f}s")

    input_images = scan_input_images()

    upper_text_color = parse_hex_color(args.upper_text_color, (0, 0, 0))
    upper_edge_color = parse_hex_color(args.upper_edge_color, (255, 220, 0))
    subtitle_text_color = parse_hex_color(args.subtitle_text_color, (255, 255, 255))
    subtitle_edge_color = parse_hex_color(args.subtitle_edge_color, (255, 20, 147))

    output_paths = render_all_stories(
        video_path, stories, transcript, out_dir,
        max_duration=max_dur,
        bg_color=args.bg_color,
        add_captions=not args.no_captions,
        input_images=input_images,
        video_title=video_title,
        review_subtitles=args.review_subtitles,
        upper_text_color=upper_text_color,
        upper_edge_color=upper_edge_color,
        subtitle_text_color=subtitle_text_color,
        subtitle_edge_color=subtitle_edge_color,
    )

    print(f"\n[5/5] Done! {len(output_paths)}/{len(stories)} stories -> {out_dir.resolve()}")
    for p in output_paths:
        print(f"   {p.name}")

    print("\n[Cleanup] Removing intermediate render files...")
    cleanup_render_dir()
    print("   Kept: downloaded video + transcript/story JSON caches in temp/")


if __name__ == "__main__":
    raise SystemExit(main())
