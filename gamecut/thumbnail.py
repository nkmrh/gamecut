"""サムネイル作成（1280x720・大きな文字入り）"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .text import font, hex_rgba, wrap

W, H = 1280, 720


def make(frame: Path, text: str, out: Path, cfg: dict, font_path: str) -> None:
    t = cfg["thumbnail"]
    img = ImageOps.fit(Image.open(frame).convert("RGB"), (W, H), Image.LANCZOS)
    img = ImageEnhance.Color(img).enhance(1.35)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = img.convert("RGBA")

    # 文字が読みやすいよう下側を暗くする
    grad = Image.new("L", (1, H))
    for y in range(H):
        grad.putpixel((0, y), int(max(0, (y - H * 0.35) / (H * 0.65)) * 200))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    shade.putalpha(grad.resize((W, H)))
    img.alpha_composite(shade)

    # 文字が収まる一番大きいサイズを探す（最大3行・高さは画面の6割まで）
    size = t["max_font_size"]
    while size > 40:
        f = font(font_path, size)
        lines = wrap(text, f, W * 0.92)
        asc, desc = f.getmetrics()
        block = len(lines) * (asc + desc) * 1.05
        if len(lines) <= 3 and block <= H * 0.6:
            break
        size -= 6
    f = font(font_path, size)
    lines = wrap(text, f, W * 0.92)[:3]
    asc, desc = f.getmetrics()
    line_h = int((asc + desc) * 1.05)
    d = ImageDraw.Draw(img)
    y = H - 40 - line_h * len(lines)
    stroke = max(4, size // 9)
    for line in lines:
        x = (W - f.getlength(line)) / 2
        # 外側に白、内側に黒の二重フチ
        d.text((x, y), line, font=f, fill=hex_rgba(t["outline_color"]),
               stroke_width=stroke + max(3, size // 22), stroke_fill=(255, 255, 255, 255))
        d.text((x, y), line, font=f, fill=hex_rgba(t["color"]),
               stroke_width=stroke, stroke_fill=hex_rgba(t["outline_color"]))
        y += line_h

    rgb = img.convert("RGB")
    for q in (92, 85, 75, 60):  # YouTube の上限 2MB に収める
        rgb.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size < 2 * 1024 * 1024:
            break
