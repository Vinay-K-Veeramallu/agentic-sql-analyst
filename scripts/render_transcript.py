"""Render a captured terminal transcript to a PNG figure.

The figures in the proposal are renderings of *captured output*, not decorated
mock-ups: each PNG is generated from the committed text file next to it, and the
caption names the command that produced it. Regenerate with:

    python scripts/render_transcript.py docs/transcripts/score.txt \
        --out docs/figures/fig3_score.png --title "make score"

Requires pillow (see requirements-dev.txt); it is not needed to run or score the
baseline.
"""

import argparse
import os
import sys

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]

BG = (18, 22, 30)
FG = (222, 226, 232)
DIM = (128, 138, 152)
ACCENT = (126, 200, 227)
PASS = (126, 214, 146)
FAIL = (240, 138, 132)
BAR = (32, 38, 48)


def pick_font(size: int):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def line_colour(text: str):
    stripped = text.strip()
    if stripped.startswith("$"):
        return ACCENT
    if "PASS" in text or "[OK]" in text or "passed" in text:
        return PASS
    if "FAIL" in text or "FABRICATED" in text or "[WARNING]" in text:
        return FAIL
    if stripped.startswith(("=", "-", "#")):
        return DIM
    return FG


def render(text_path: str, out_path: str, title: str, font_size: int = 15,
           max_width: int = 132) -> str:
    from PIL import Image, ImageDraw

    with open(text_path, "r", encoding="utf-8") as handle:
        lines = [ln.rstrip("\n") for ln in handle]
    lines = [ln if len(ln) <= max_width else ln[:max_width - 1] + "\u2026"
             for ln in lines]

    font = pick_font(font_size)
    title_font = pick_font(font_size - 2)

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    char_w = probe.textlength("M" * 10, font=font) / 10 or font_size * 0.6
    line_h = int(font_size * 1.55)
    pad, bar_h = 18, int(font_size * 2.0)

    width = int(char_w * (max(len(ln) for ln in lines) if lines else 40)) + pad * 2
    width = max(width, 720)
    height = bar_h + pad + line_h * max(len(lines), 1) + pad

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, width, bar_h], fill=BAR)
    for index, colour in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        cx = 16 + index * 18
        draw.ellipse([cx, bar_h // 2 - 5, cx + 10, bar_h // 2 + 5], fill=colour)
    draw.text((78, bar_h // 2 - font_size // 2 - 1), title, font=title_font, fill=DIM)

    y = bar_h + pad
    for text in lines:
        draw.text((pad, y), text, font=font, fill=line_colour(text))
        y += line_h

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    image.save(out_path)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript")
    parser.add_argument("--out", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--font-size", type=int, default=15)
    args = parser.parse_args()

    try:
        path = render(args.transcript, args.out, args.title or
                      os.path.basename(args.transcript), args.font_size)
    except ImportError:
        print("[ERROR] pillow is required: pip install -r requirements-dev.txt")
        return 1
    print(f"[OK] wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
