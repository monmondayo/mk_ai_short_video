"""Text wrapping and font fitting for Japanese/CJK and Latin text."""

from janome.tokenizer import Tokenizer
from PIL import Image, ImageDraw, ImageFont

from .constants import STROKE_W, TARGET_W
from .fonts import load_font

JANOME_TOKENIZER = Tokenizer()


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


def _measure(text: str, font: ImageFont.FreeTypeFont) -> float:
    """Return visual width of text including stroke on both sides."""
    _d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return _d.textlength(text, font=font) + STROKE_W * 2


def wrap_pixels(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Wrap text to fit within max_w pixels per line.
    For Japanese (no spaces), wraps at Janome morpheme boundaries to avoid mid-word cuts."""
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
                    sub = _char_wrap(token)
                    lines.extend(sub[:-1])
                    current = sub[-1] if sub else ""
                    current_tokens = [current] if current else []
    if current:
        lines.append(current)
    return lines


def fit_text_font(
    lines: list[str], font_path: str, start_size: int, min_size: int = 24
) -> tuple[ImageFont.FreeTypeFont, int]:
    """Return (font, line_height) — the largest font <= start_size where every line
    fits within TARGET_W pixels (stroke included)."""
    _img = Image.new("RGB", (1, 1))
    _draw = ImageDraw.Draw(_img)
    max_w = TARGET_W - STROKE_W * 2 - 10

    size = max(start_size, min_size)
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


def wrap_and_fit(
    text: str, font_path: str, start_size: int,
    max_lines: int = 2, min_size: int = 16,
) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Find the largest font <= start_size where text fits in max_lines lines.
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
