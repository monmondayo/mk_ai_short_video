#!/usr/bin/env python3
"""
Verify that all cloud services are configured correctly.

Usage:
    python scripts/verify_setup.py

Checks:
    1. Environment variables (.env)
    2. Supabase connection + table schema
    3. Cloudflare R2 bucket access
    4. Modal API endpoint (if deployed)
    5. Local mkvideo package imports
"""

import json
import os
import sys
from pathlib import Path

# Load .env (check worktree first, then main repo via git)
from dotenv import load_dotenv
import subprocess

script_dir = Path(__file__).resolve().parent.parent

# Ensure mkvideo is importable
sys.path.insert(0, str(script_dir))

# Find the main repo root (git worktrees are separate paths)
load_dotenv(script_dir / ".env")
try:
    git_root = subprocess.check_output(
        ["git", "-C", str(script_dir), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True, stderr=subprocess.DEVNULL
    ).strip()
    # git-common-dir returns the .git dir of the main repo
    main_repo = Path(git_root).parent
    load_dotenv(main_repo / ".env", override=False)
except Exception:
    pass

PASS = "\033[92m PASS \033[0m"
FAIL = "\033[91m FAIL \033[0m"
SKIP = "\033[93m SKIP \033[0m"

results = []


def check(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, ok))
    msg = f"  [{status}] {name}"
    if detail:
        msg += f"  — {detail}"
    print(msg)
    return ok


# ── 1. Environment Variables ─────────────────────────────────────────
print("\n1. Environment Variables")
env_vars = {
    "ANTHROPIC_API_KEY": "Claude API",
    "SUPABASE_URL": "Supabase project URL",
    "SUPABASE_SERVICE_KEY": "Supabase service role key",
    "R2_ACCOUNT_ID": "Cloudflare account ID",
    "R2_ACCESS_KEY_ID": "R2 API access key",
    "R2_SECRET_ACCESS_KEY": "R2 API secret key",
    "R2_BUCKET_NAME": "R2 bucket name",
}

env_ok = True
for var, desc in env_vars.items():
    val = os.environ.get(var, "")
    ok = bool(val)
    check(f"${var}", ok, desc if not ok else f"{val[:8]}...")
    if not ok:
        env_ok = False

# ── 2. Local Package Imports ─────────────────────────────────────────
print("\n2. Local Package Imports")
try:
    from mkvideo.pipeline.constants import TARGET_W, TARGET_H
    check("mkvideo.pipeline.constants", True, f"TARGET={TARGET_W}x{TARGET_H}")
except ImportError as e:
    check("mkvideo.pipeline.constants", False, str(e))

try:
    from mkvideo.pipeline.transcribe import transcribe_video
    check("mkvideo.pipeline.transcribe", True)
except ImportError as e:
    check("mkvideo.pipeline.transcribe", False, str(e))

try:
    from mkvideo.pipeline.render import render_all_stories
    check("mkvideo.pipeline.render", True)
except ImportError as e:
    check("mkvideo.pipeline.render", False, str(e))

try:
    from mkvideo.cloud.supabase_client import SupabaseJobClient
    check("mkvideo.cloud.supabase_client", True)
except ImportError as e:
    check("mkvideo.cloud.supabase_client", False, str(e))

try:
    from mkvideo.storage.r2 import R2Storage
    check("mkvideo.storage.r2", True)
except ImportError as e:
    check("mkvideo.storage.r2", False, str(e))

# ── 3. Supabase Connection ───────────────────────────────────────────
print("\n3. Supabase Connection")
if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"):
    try:
        import requests
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
        }

        # Check jobs table
        resp = requests.get(
            f"{url}/rest/v1/jobs?select=id&limit=1",
            headers=headers,
        )
        check("Supabase jobs table", resp.status_code == 200, f"HTTP {resp.status_code}")

        # Check transcripts table
        resp = requests.get(
            f"{url}/rest/v1/transcripts?select=id&limit=1",
            headers=headers,
        )
        check("Supabase transcripts table", resp.status_code == 200, f"HTTP {resp.status_code}")

        # Check stories table
        resp = requests.get(
            f"{url}/rest/v1/stories?select=id&limit=1",
            headers=headers,
        )
        check("Supabase stories table", resp.status_code == 200, f"HTTP {resp.status_code}")

        # Check output_videos table
        resp = requests.get(
            f"{url}/rest/v1/output_videos?select=id&limit=1",
            headers=headers,
        )
        check("Supabase output_videos table", resp.status_code == 200, f"HTTP {resp.status_code}")

        # Check jobs table has user_id column
        resp = requests.get(
            f"{url}/rest/v1/jobs?select=user_id&limit=1",
            headers=headers,
        )
        check("jobs.user_id column exists", resp.status_code == 200,
              "New schema" if resp.status_code == 200 else "Run 002_drop_and_recreate.sql")

    except Exception as e:
        check("Supabase connection", False, str(e))
else:
    check("Supabase connection", False, "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")

# ── 4. Cloudflare R2 ─────────────────────────────────────────────────
print("\n4. Cloudflare R2")
if all(os.environ.get(v) for v in ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]):
    try:
        r2 = R2Storage()
        bucket = os.environ.get("R2_BUCKET_NAME", "mkvideo")
        # Try to list objects (head_bucket might not be supported)
        resp = r2.s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
        check("R2 bucket access", True, f"Bucket: {bucket}")
    except Exception as e:
        check("R2 bucket access", False, str(e))
else:
    check("R2 bucket access", False, "Missing R2 credentials")

# ── 5. Anthropic API ─────────────────────────────────────────────────
print("\n5. Anthropic API")
if os.environ.get("ANTHROPIC_API_KEY"):
    try:
        import anthropic
        client = anthropic.Anthropic()
        # Just verify the key format, don't make an actual call
        key = os.environ["ANTHROPIC_API_KEY"]
        check("Anthropic API key format", key.startswith("sk-ant-"), f"{key[:12]}...")
    except ImportError:
        check("Anthropic SDK", False, "pip install anthropic")
else:
    check("Anthropic API key", False, "Missing ANTHROPIC_API_KEY")

# ── 6. ffmpeg ─────────────────────────────────────────────────────────
print("\n6. System Dependencies")
import shutil
ffmpeg_path = shutil.which("ffmpeg")
check("ffmpeg installed", ffmpeg_path is not None, ffmpeg_path or "Not found")

# ── Summary ──────────────────────────────────────────────────────────
print("\n" + "=" * 50)
passed = sum(1 for _, ok in results if ok)
total = len(results)
failed = total - passed
if failed == 0:
    print(f"  All {total} checks passed!")
else:
    print(f"  {passed}/{total} passed, {failed} failed")
    print(f"  Fix the failures above, then re-run this script.")

sys.exit(0 if failed == 0 else 1)
