"""文字の描画（Pillow）と、動画に重ねる文字レイヤーの作成。

ffmpeg のビルドによっては文字描画機能がないため、文字はすべて透過 PNG に描いて
ffconcat でつなぎ、1つの overlay で重ねる。
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = {
    "darwin": [
        ("/System/Library/Fonts", ["ヒラギノ角ゴシック W8.ttc", "ヒラギノ角ゴシック W7.ttc",
                                   "ヒラギノ角ゴシック W6.ttc", "ヒラギノ角ゴシック W5.ttc"]),
        ("/Library/Fonts", ["NotoSansJP-Black.otf", "NotoSansJP-Bold.otf", "Arial Unicode.ttf"]),
    ],
    "nt": [
        (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
         ["YuGothB.ttc", "BIZ-UDGothicB.ttc", "meiryob.ttc", "meiryo.ttc", "msgothic.ttc"]),
    ],
    "linux": [
        ("/usr/share/fonts/opentype/noto", ["NotoSansCJK-Black.ttc", "NotoSansCJK-Bold.ttc"]),
        ("/usr/share/fonts/noto-cjk", ["NotoSansCJK-Bold.ttc"]),
    ],
}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def find_font(configured: str = "") -> str:
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return str(p)
        print(f"  ! 指定されたフォントが見つかりません: {configured}（自動で探します）")
    key = "nt" if os.name == "nt" else ("darwin" if os.uname().sysname == "Darwin" else "linux")
    for folder, names in _FONT_CANDIDATES[key]:
        if not os.path.isdir(folder):
            continue
        # macOS のファイル名は NFD のことがあるので正規化して比較する
        files = {_nfc(f): os.path.join(folder, f) for f in os.listdir(folder)}
        for n in names:
            if _nfc(n) in files:
                return files[_nfc(n)]
    raise RuntimeError("日本語フォントが見つかりません。gamecut.yaml の font.path にフォントファイルを指定してください。")


_FONT_CACHE: dict = {}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def hex_rgba(color: str, alpha: int = 255) -> tuple:
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


_TOKEN = re.compile(r"[A-Za-z0-9'’\-_.,!?%&]+|\s+|.", re.S)
_NO_LINE_START = set("、。，．！？!?」』）)]】ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ…〜")


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for tok in _TOKEN.findall(para):
            if f.getlength(line + tok) <= max_width or not line.strip():
                line += tok
            elif tok[0] in _NO_LINE_START:  # 句読点などは行頭に来ないよう前の行に付ける
                line += tok
            else:
                lines.append(line.rstrip())
                line = tok.lstrip()
        lines.append(line.rstrip())
    return [l for l in lines if l] or [""]


@dataclass
class Item:
    text: str
    size: int
    y: float  # 文字の塊の中心の高さ（画面の高さに対する割合）
    color: str = "#FFFFFF"
    outline: str = "#000000"
    max_width: float = 0.9
    max_lines: int = 2
    band: tuple | None = None  # 背景の帯 (r, g, b, a)
    button: str | None = None  # ボタンの背景色（指定するとボタン風に描く）


@dataclass
class Cue:
    start: float
    end: float
    items: list[Item] = field(default_factory=list)


def _fit(item: Item, font_path: str, max_w: float) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = item.size
    while True:
        f = font(font_path, size)
        lines = wrap(item.text, f, max_w)
        if len(lines) <= item.max_lines or size <= item.size * 0.55:
            return f, lines[: item.max_lines]
        size = int(size * 0.9)


def draw_item(img: Image.Image, item: Item, font_path: str) -> None:
    W, H = img.size
    f, lines = _fit(item, font_path, W * item.max_width)
    stroke = max(2, f.size // 11)
    asc, desc = f.getmetrics()
    line_h = asc + desc
    gap = int(f.size * 0.15)
    block_h = len(lines) * line_h + (len(lines) - 1) * gap
    top = int(H * item.y - block_h / 2)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if item.band:
        pad = int(f.size * 0.45)
        d.rectangle([0, top - pad, W, top + block_h + pad], fill=item.band)
    if item.button:
        tw = max(f.getlength(l) for l in lines)
        px, py = int(f.size * 0.9), int(f.size * 0.45)
        box = [W / 2 - tw / 2 - px, top - py, W / 2 + tw / 2 + px, top + block_h + py]
        d.rounded_rectangle(box, radius=int(f.size * 0.35), fill=hex_rgba(item.button))
        stroke = 0
    y = top
    for line in lines:
        x = (W - f.getlength(line)) / 2
        d.text((x, y), line, font=f, fill=hex_rgba(item.color),
               stroke_width=stroke, stroke_fill=hex_rgba(item.outline))
        y += line_h + gap
    img.alpha_composite(layer)


def render_frame(size: tuple[int, int], items: list[Item], font_path: str) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for it in items:
        draw_item(img, it, font_path)
    return img


def build_overlay(cues: list[Cue], duration: float, size: tuple[int, int],
                  workdir: Path, font_path: str) -> Path:
    """時間ごとに表示する文字をまとめた透過 PNG 列と、それをつなぐ ffconcat ファイルを作る"""
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("ov_*.png"):
        old.unlink()
    duration = round(duration, 3)
    spans = [(round(c.start, 3), round(c.end, 3)) for c in cues]  # 比較は丸めた時刻どうしで行う
    points = {0.0, duration}
    for s, e in spans:
        for t in (s, e):
            if 0 < t < duration:
                points.add(t)
    points = sorted(points)
    segments: list[tuple[tuple, float]] = []
    for a, b in zip(points, points[1:]):
        active = tuple(i for i, (s, e) in enumerate(spans) if s <= a < e)
        if segments and segments[-1][0] == active:
            segments[-1] = (active, segments[-1][1] + (b - a))
        else:
            segments.append((active, b - a))
    names: dict[tuple, str] = {}
    lines = ["ffconcat version 1.0"]
    for active, dur in segments:
        if active not in names:
            name = f"ov_{len(names):05d}.png"
            items = [it for i in active for it in cues[i].items]
            render_frame(size, items, font_path).save(workdir / name, compress_level=1)
            names[active] = name
        lines += [f"file '{names[active]}'", f"duration {dur:.3f}"]
    lines.append(f"file '{names[segments[-1][0]]}'")
    out = workdir / "overlay.ffconcat"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
