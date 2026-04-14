"""Step 2: Transcribe video with Whisper and proofread with Claude."""

import json
import os
import re
from pathlib import Path

import anthropic
import whisper


def transcribe_video(video_path: Path, model_name: str = "medium", language: str | None = "ja") -> dict:
    lang_hint = f", language={language}" if language else ""
    print(f"\n[2/5] Transcribing with Whisper ({model_name}{lang_hint})...")
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


def proofread_transcript(transcript: dict) -> dict:
    """Use Claude to fix kanji/grammar errors caused by speech recognition."""
    print("   Proofreading transcript with Claude Haiku...")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("   ANTHROPIC_API_KEY not set, skipping proofreading.")
        return transcript

    client = anthropic.Anthropic(api_key=api_key)
    segments = transcript["segments"]
    CHUNK_SIZE = 40
    CONTEXT_SIZE = 5
    corrected_segments: list[dict] = []

    for chunk_start in range(0, len(segments), CHUNK_SIZE):
        chunk = segments[chunk_start : chunk_start + CHUNK_SIZE]
        texts = [s["text"] for s in chunk]

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
# Step 2.6: Manual transcript review
# ──────────────────────────────────────────
def export_review_txt(transcript: dict, path: Path) -> None:
    """Export transcript segments to a plain-text file for manual editing."""
    lines = ["# 誤字を修正してください。=== の行は変更しないでください。\n"]
    for i, seg in enumerate(transcript["segments"]):
        t0, t1 = seg["start"], seg["end"]
        lines.append(f"=== {i} [{t0:.1f}s - {t1:.1f}s] ===")
        lines.append(seg["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def import_review_txt(path: Path, transcript: dict) -> dict:
    """Read back an edited review file and update transcript segment texts."""
    header_re = re.compile(r"^=== (\d+) \[[\d.]+s - [\d.]+s\] ===$")
    segments = [dict(s) for s in transcript["segments"]]
    current_idx: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_idx is not None and current_idx < len(segments):
            text = "\n".join(current_lines).strip()
            if text:
                segments[current_idx]["text"] = text

    for line in path.read_text(encoding="utf-8").splitlines():
        m = header_re.match(line)
        if m:
            flush()
            current_idx = int(m.group(1))
            current_lines = []
        elif current_idx is not None and not line.startswith("#"):
            current_lines.append(line)
    flush()

    return {"text": "".join(s["text"] for s in segments), "segments": segments}
