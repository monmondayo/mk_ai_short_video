"""Font detection, loading, and text sanitization."""

from pathlib import Path

from PIL import ImageFont


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


def find_system_font() -> str:
    """Find a pop/bold CJK-capable font on macOS or Linux."""
    candidates = [
        # ── macOS ──
        # 丸ゴシック: rounded, pop/fun feel
        "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
        # Heavy gothic for impact
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        # Latin fallbacks (macOS)
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/ArialHB.ttc",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        # ── Linux (Noto CJK, installed via apt: fonts-noto-cjk) ──
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        # Linux Latin fallbacks
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
