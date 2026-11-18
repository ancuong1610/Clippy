"""Find a heavy display font on macOS or Linux; fall back to Pillow's built-in font."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_DIRS = ["/System/Library/Fonts/Supplemental", "/System/Library/Fonts", "/Library/Fonts",
         str(Path.home() / "Library/Fonts"), "/usr/share/fonts", "/usr/local/share/fonts",
         str(Path.home() / ".local/share/fonts"), "C:/Windows/Fonts"]

# first match wins: the approved look uses the macOS fonts, the rest are close free stand-ins
CANDIDATES = {
    "black": ["Arial Black.ttf", "ariblk.ttf", "Anton-Regular.ttf", "DejaVuSans-Bold.ttf"],
    "bold": ["Arial Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "impact": ["Impact.ttf", "impact.ttf", "Anton-Regular.ttf", "DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=None)
def font_path(style: str) -> str | None:
    for name in CANDIDATES[style]:
        for d in _DIRS:
            hits = list(Path(d).rglob(name)) if Path(d).is_dir() else []
            if hits:
                return str(hits[0])
    return None


def font(style: str, size: int) -> ImageFont.FreeTypeFont:
    p = font_path(style)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)
