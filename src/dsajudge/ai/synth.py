"""Synthetic statement images, rendered from text with Pillow (no real photos in the repo).

``10^{5}`` in a line is drawn as a raised, smaller "5" like a printed exponent; leading spaces
are kept so indented code stays indented. Used by `dsa doctor --models` and the tests.
"""

from __future__ import annotations

import re
from pathlib import Path

SUP = re.compile(r"\^\{([^}]*)\}")


def render_text_image(
    lines: list[str], out: Path, *, size: int = 30, width: int = 1100, pad: int = 28
) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=size)
    small = ImageFont.load_default(size=max(10, int(size * 0.62)))
    line_h = int(size * 1.55)
    img = Image.new("RGB", (width, pad * 2 + line_h * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    y = pad
    for line in lines:
        x = float(pad)
        pos = 0
        for m in SUP.finditer(line):
            chunk = line[pos : m.start()]
            draw.text((x, y), chunk, fill="black", font=font)
            x += draw.textlength(chunk, font=font)
            draw.text((x, y - size * 0.32), m.group(1), fill="black", font=small)
            x += draw.textlength(m.group(1), font=small)
            pos = m.end()
        draw.text((x, y), line[pos:], fill="black", font=font)
        y += line_h
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
