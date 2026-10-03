"""BGM：曲の自動ダウンロード・曲調の分類・場面ごとの自動選曲"""
from __future__ import annotations

import json
import random
import subprocess
import urllib.parse
from pathlib import Path

import numpy as np

from . import ff, setup
from .config import AUDIO_EXTS, HOME_DIR, resolve
from .highlights import smooth

MOODS = ["まったり", "ふつう", "激しい"]
LIB_DIR = HOME_DIR / "bgm"
CATALOG_URL = "https://incompetech.com/music/royalty-free/pieces.json"
MP3_URL = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
CREDIT = ('"{title}" Kevin MacLeod (incompetech.com)\n'
          "Licensed under Creative Commons: By Attribution 4.0\n"
          "http://creativecommons.org/licenses/by/4.0/")

_GLOOMY = {"Somber", "Eerie", "Unnerving"}


# ゲーム実況に合わないもの（クラシック・季節もの・ホラー調など）
_EXCLUDE_GENRES = {"4", "9", "10"}
_EXCLUDE_WORDS = ("christmas", "holiday", "halloween", "horror", "spooky", "wedding", "funeral", "national anthem")


def _mood_of(piece: dict) -> str | None:
    feel = {f.strip() for f in (piece.get("feel") or "").split(",")}
    text = f"{piece.get('title', '')} {piece.get('description', '')}".lower()
    if str(piece.get("genre")) in _EXCLUDE_GENRES or any(w in text for w in _EXCLUDE_WORDS):
        return None
    try:
        bpm = float(piece.get("bpm") or 0)
    except ValueError:
        bpm = 0
    if feel & {"Action", "Driving", "Intense", "Epic", "Aggressive"} and bpm >= 110 and not feel & _GLOOMY:
        return "激しい"
    if feel & _GLOOMY or "Dark" in feel:
        return None
    if feel & {"Bright", "Bouncy", "Grooving", "Uplifting", "Humorous"}:
        return "ふつう"
    if feel & {"Relaxed", "Calming"}:
        return "まったり"
    return None


def _seconds(length: str) -> float:
    try:
        sec = 0.0
        for p in length.split(":"):
            sec = sec * 60 + float(p)
        return sec
    except (ValueError, AttributeError):
        return 0


def ensure_library(per_mood: int) -> list[dict]:
    """初回だけ incompetech（CC BY 4.0）から曲調ごとに曲をダウンロードする"""
    lib_file = LIB_DIR / "library.json"
    if lib_file.exists():
        return json.loads(lib_file.read_text(encoding="utf-8"))
    print(f"  ▶ BGM を初回ダウンロードします（曲調ごとに{per_mood}曲・Kevin MacLeod / CC BY 4.0）", flush=True)
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    cat = LIB_DIR / "pieces.json"
    setup._download(CATALOG_URL, cat)
    pieces = json.loads(cat.read_text(encoding="utf-8"))
    rng = random.Random(0)
    rng.shuffle(pieces)
    tracks: list[dict] = []
    for mood in MOODS:
        pool = [p for p in pieces if _mood_of(p) == mood and 90 <= _seconds(p.get("length", "")) <= 360]
        got = 0
        for p in pool:
            if got >= per_mood:
                break
            dest = LIB_DIR / mood / p["filename"]
            dest.parent.mkdir(exist_ok=True)
            try:
                if not dest.exists():
                    setup._download(MP3_URL + urllib.parse.quote(p["filename"]), dest)
            except Exception as e:  # 1曲失敗しても続ける
                print(f"  ! ダウンロード失敗: {p['title']} ({e})")
                dest.unlink(missing_ok=True)
                continue
            tracks.append({"path": str(dest), "title": p["title"], "mood": mood,
                           "credit": CREDIT.format(title=p["title"])})
            got += 1
    lib_file.write_text(json.dumps(tracks, ensure_ascii=False, indent=1), encoding="utf-8")
    cat.unlink(missing_ok=True)
    return tracks


def _activity(path: Path) -> float:
    """曲の激しさの目安：50ms ごとの音量の立ち上がりの多さ"""
    cmd = [ff.tools()[0], "-hide_banner", "-loglevel", "error", "-t", "120", "-i", str(path),
           "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, dtype=np.int16).astype(np.float32)
    hop = 400
    n = len(x) // hop
    if n < 10:
        return 0.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-6
    rise = np.clip(np.diff(rms), 0, None)
    return float(rise.mean() / rms.mean())


def user_tracks(cfg: dict) -> list[dict]:
    """素材フォルダの bgm/ にある自分の曲。曲調フォルダに入っていない曲は解析して分類する"""
    folder = resolve(cfg, cfg["bgm"]["folder"])
    if not folder.exists():
        return []
    cache_file = folder / ".分類キャッシュ.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    tracks = []
    for f in sorted(folder.rglob("*")):
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        mood = f.parent.name if f.parent.name in MOODS else None
        if mood is None:
            key = f"{f.name}:{f.stat().st_size}"
            if key not in cache:
                a = _activity(f)
                cache[key] = "激しい" if a > 0.16 else ("ふつう" if a > 0.10 else "まったり")
            mood = cache[key]
        tracks.append({"path": str(f), "title": f.stem, "mood": mood, "credit": ""})
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return tracks


def all_tracks(cfg: dict) -> list[dict]:
    tracks = user_tracks(cfg)
    if cfg["bgm"]["auto_download"]:
        try:
            tracks += ensure_library(cfg["bgm"]["tracks_per_mood"])
        except Exception as e:
            print(f"  ! BGM のダウンロードに失敗しました: {e}")
    return [t for t in tracks if Path(t["path"]).exists()]


def level_of(value: float) -> str:
    return "激しい" if value >= 0.6 else ("ふつう" if value >= 0.35 else "まったり")


def plan_sections(score: np.ndarray, total: float, min_len: float, fixed_mood: str | None) -> list[dict]:
    """盛り上がり度から、BGM を切り替える区間を決める"""
    if fixed_mood or len(score) < 2:
        return [{"start": 0.0, "end": total, "mood": fixed_mood or "ふつう"}]
    s = smooth(score, 30)
    runs: list[list] = []
    for i, v in enumerate(s):
        m = level_of(float(v))
        if runs and runs[-1][0] == m:
            runs[-1][2] = i + 1
        else:
            runs.append([m, i, i + 1])
    # 短すぎる区間は隣とくっつける
    while len(runs) > 1:
        idx = min(range(len(runs)), key=lambda k: runs[k][2] - runs[k][1])
        if runs[idx][2] - runs[idx][1] >= min_len:
            break
        if idx == 0:
            nb = 1
        elif idx == len(runs) - 1:
            nb = idx - 1
        else:
            nb = idx - 1 if runs[idx - 1][2] - runs[idx - 1][1] >= runs[idx + 1][2] - runs[idx + 1][1] else idx + 1
        lo, hi = min(idx, nb), max(idx, nb)
        runs[lo] = [runs[nb][0], runs[lo][1], runs[hi][2]]
        del runs[hi]
        merged = [runs[0]]
        for r in runs[1:]:
            if r[0] == merged[-1][0]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
    sections = [{"start": float(r[1]), "end": float(r[2]), "mood": r[0]} for r in runs]
    sections[0]["start"] = 0.0
    sections[-1]["end"] = total  # エンディングも最後の曲のまま流す
    return sections


class Picker:
    """曲調ごとに、なるべく同じ曲が続かないよう順番に曲を選ぶ"""

    def __init__(self, tracks: list[dict], seed: str):
        self.rng = random.Random(seed)
        self.pools: dict[str, list[dict]] = {}
        for m in MOODS:
            pool = [t for t in tracks if t["mood"] == m]
            self.rng.shuffle(pool)
            self.pools[m] = pool
        self.pos = {m: 0 for m in MOODS}
        self.last = None

    def pick(self, mood: str) -> dict | None:
        order = [mood] + [m for m in (["ふつう", "まったり", "激しい"]) if m != mood]
        for m in order:  # その曲調の曲がなければ近い曲調から
            pool = self.pools[m]
            if not pool:
                continue
            t = pool[self.pos[m] % len(pool)]
            self.pos[m] += 1
            if t is self.last and len(pool) > 1:
                t = pool[self.pos[m] % len(pool)]
                self.pos[m] += 1
            self.last = t
            return t
        return None


def render_track(sections: list[dict], workdir: Path, out_wav: Path, fade: float = 1.5) -> None:
    """区間ごとの曲をフェードでつないだ BGM トラックを作る"""
    workdir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, sec in enumerate(sections):
        d = sec["end"] - sec["start"]
        af = ["loudnorm=I=-18:TP=-2:LRA=11", "aresample=48000",
              "aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo"]
        if i > 0:
            af.append(f"afade=t=in:d={fade}")
        if i < len(sections) - 1:
            af.append(f"afade=t=out:st={max(0, d - fade):.3f}:d={fade}")
        part = workdir / f"bgm_{i:03d}.wav"
        ff.run(["-stream_loop", "-1", "-i", sec["track"], "-t", f"{d:.3f}", "-af", ",".join(af), part],
               desc=f"BGM {i + 1}/{len(sections)}（{sec['mood']}）: {sec['title']}", quiet=True)
        parts.append(part)
    lst = workdir / "bgm.ffconcat"
    lst.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    ff.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out_wav], quiet=True)
    for p in parts:
        p.unlink(missing_ok=True)


def credits(sections: list[dict]) -> str:
    seen, lines = set(), []
    for s in sections:
        if s.get("credit") and s["credit"] not in seen:
            seen.add(s["credit"])
            lines.append(s["credit"])
    return ("\n\n♪ BGM\n" + "\n\n".join(lines)) if lines else ""


def build(score: np.ndarray, total: float, cfg: dict, side: dict, seed: str,
          workdir: Path, out_wav: Path, tracks: list[dict]) -> list[dict] | None:
    """BGM トラックを作り、使った区間と曲の一覧を返す（曲がなければ None）"""
    if not tracks and not side.get("bgm_file"):
        print("  ! 使える BGM がないので BGM なしで作ります")
        return None
    if side.get("bgm_file"):
        p = Path(side["bgm_file"]).expanduser()
        sections = [{"start": 0.0, "end": total, "mood": "指定", "track": str(p), "title": p.stem, "credit": ""}]
    else:
        sections = plan_sections(score, total, cfg["bgm"]["min_section"], side.get("bgm_mood"))
        picker = Picker(tracks, seed)
        for s in sections:
            t = picker.pick(s["mood"])
            s.update(track=t["path"], title=t["title"], credit=t["credit"])
    render_track(sections, workdir, out_wav)
    return sections
