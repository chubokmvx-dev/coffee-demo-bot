"""Мінімалістична картинка до акційного повідомлення: малюється локально, без зовнішніх сервісів."""
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
W = H = 1080
BG, INK, ACCENT, MUTED = (244, 237, 227), (43, 29, 20), (196, 98, 45), (125, 108, 95)
M = 96
_EMOJI = re.compile("[\U0001F000-\U0001FAFF←-⯿️‍]")


def _font(bold: bool, size: int):
    return ImageFont.truetype(str(ASSETS / ("Inter-Bold.otf" if bold else "Inter-Regular.otf")), size)


def _wrap(d, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def split_text(text: str):
    """Заголовок = перше речення, решта — підзаголовок. Емодзі прибираються (шрифт їх не малює)."""
    clean = " ".join(_EMOJI.sub("", text).split())
    m = re.match(r"(.+?[.!?])\s+(.+)", clean)
    return (m.group(1), m.group(2)) if m else (clean, "")


def _cup(d, x, y, s):
    """Проста чашка з блюдцем, геометрією."""
    d.arc((x + s * 0.68, y + s * 0.18, x + s * 1.08, y + s * 0.58), -80, 80, fill=ACCENT, width=int(s * 0.08))
    d.rounded_rectangle((x, y, x + s * 0.8, y + s * 0.7), radius=int(s * 0.22), fill=ACCENT)
    d.rectangle((x, y, x + s * 0.8, y + s * 0.2), fill=ACCENT)
    d.rounded_rectangle((x - s * 0.12, y + s * 0.76, x + s * 0.92, y + s * 0.82), radius=int(s * 0.03), fill=INK)


def render(text: str, shop: str) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    head, sub = split_text(text)
    d.text((M, M), shop.upper(), font=_font(True, 30), fill=INK)
    _cup(d, W - M - 140, M - 6, 110)
    d.rectangle((M, 190, M + 72, 196), fill=ACCENT)
    # заголовок: найбільший розмір, що вміщається у 4 рядки
    max_w = W - 2 * M
    for size in range(88, 40, -4):
        f = _font(True, size)
        lines = _wrap(d, head, f, max_w)
        if len(lines) <= 4 and all(d.textlength(l, font=f) <= max_w for l in lines):
            break
    lh = int(size * 1.12)
    sf = _font(False, 38)
    slines = _wrap(d, sub, sf, max_w)[:3] if sub else []
    block = len(lines) * lh + (40 + len(slines) * 54 if slines else 0)
    y = H - M - 20 - block
    for l in lines:
        d.text((M, y), l, font=f, fill=INK)
        y += lh
    y += 40 - 10
    for l in slines:
        d.text((M, y), l, font=sf, fill=MUTED)
        y += 54
    buf = BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()
