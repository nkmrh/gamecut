"""ffmpeg / ffprobe まわりの処理"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import setup

_TOOLS: dict[str, str] = {}


def tools() -> tuple[str, str]:
    if not _TOOLS:
        _TOOLS["ffmpeg"], _TOOLS["ffprobe"] = setup.ensure_ffmpeg()
    return _TOOLS["ffmpeg"], _TOOLS["ffprobe"]


def ffmpeg_major() -> int:
    out = subprocess.run([tools()[0], "-hide_banner", "-version"], capture_output=True, text=True).stdout
    m = re.search(r"ffmpeg version n?(\d+)\.", out)
    return int(m.group(1)) if m else 99  # 開発版ビルドは新しいものとみなす


def run(args: list, desc: str = "", cwd: Path | None = None, quiet: bool = False) -> None:
    """ffmpeg を実行する（quiet でなければ進み具合を ffmpeg の stats 表示で出す）"""
    if desc:
        print(f"  ▶ {desc}", flush=True)
    stats = "-nostats" if quiet else "-stats"
    cmd = [tools()[0], "-hide_banner", "-y", "-loglevel", "error", stats, *map(str, args)]
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg が失敗しました（{desc or 'ffmpeg'}）")


def filter_script_args(script: Path) -> list[str]:
    return ["-/filter_complex", str(script)] if ffmpeg_major() >= 7 else ["-filter_complex_script", str(script)]


@dataclass
class Info:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: Path) -> Info:
    out = subprocess.run(
        [tools()[1], "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"動画を読み込めません: {path}")
    data = json.loads(out.stdout)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise RuntimeError(f"映像が含まれていません: {path}")
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    duration = float(data["format"].get("duration") or v.get("duration") or 0)
    return Info(duration, int(v["width"]), int(v["height"]), fps or 30.0, has_audio)


def detect_silence(path: Path, noise_db: float, min_dur: float) -> list[tuple[float, float]]:
    cmd = [tools()[0], "-hide_banner", "-nostats", "-i", str(path), "-vn",
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    silences, start = [], None
    for line in err.splitlines():
        if m := re.search(r"silence_start: (-?[\d.]+)", line):
            start = max(0.0, float(m.group(1)))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:
        silences.append((start, float("inf")))
    return silences


def audio_envelope(path: Path, rate: int = 8000) -> np.ndarray:
    """1秒ごとの音量（dB）"""
    cmd = [tools()[0], "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
           "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(samples) // rate
    if n == 0:
        return np.full(1, -90.0)
    rms = np.sqrt(np.mean(samples[: n * rate].reshape(n, rate) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def motion_envelope(path: Path, fps: int = 2) -> np.ndarray:
    """1秒ごとの画面の動きの大きさ（縮小グレー画像の前後差分）"""
    w, h = 64, 36
    cmd = [tools()[0], "-hide_banner", "-loglevel", "error", "-i", str(path), "-an",
           "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8)
    n = len(frames) // (w * h)
    if n < 2:
        return np.zeros(1)
    frames = frames[: n * w * h].reshape(n, w * h).astype(np.float32)
    diff = np.concatenate([[0.0], np.abs(np.diff(frames, axis=0)).mean(axis=1)])
    secs = n // fps
    if secs == 0:
        return np.array([diff.mean()])
    return diff[: secs * fps].reshape(secs, fps).mean(axis=1)


def extract_frame(path: Path, t: float, out: Path) -> None:
    run(["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-update", "1", out], quiet=True)


def decode_audio(path: Path, out_wav: Path, seconds: float | None = None) -> None:
    args = ["-i", path, "-vn", "-ac", "2", "-ar", "48000"]
    if seconds:
        args += ["-t", f"{seconds:.3f}"]
    run([*args, out_wav])


_ENCODER_CACHE: dict[str, bool] = {}


def _encoder_ok(name: str) -> bool:
    if name not in _ENCODER_CACHE:
        cmd = [tools()[0], "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               "color=c=black:s=256x256:d=0.1", "-c:v", name, "-f", "null", "-"]
        _ENCODER_CACHE[name] = subprocess.run(cmd, capture_output=True).returncode == 0
    return _ENCODER_CACHE[name]


def encoder_args(cfg: dict, intermediate: bool = False) -> list[str]:
    v = cfg["video"]
    enc = v["encoder"]
    if intermediate:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p"]
    if enc == "auto":
        if sys.platform == "darwin" and _encoder_ok("h264_videotoolbox"):
            enc = "h264_videotoolbox"
        elif _encoder_ok("h264_nvenc"):
            enc = "h264_nvenc"
        else:
            enc = "libx264"
    if enc == "h264_videotoolbox":
        args = ["-c:v", enc, "-b:v", v["bitrate"]]
    elif enc == "h264_nvenc":
        args = ["-c:v", enc, "-preset", "p5", "-cq", str(v["crf"]), "-b:v", "0"]
    else:
        args = ["-c:v", "libx264", "-preset", v["preset"], "-crf", str(v["crf"])]
    return [*args, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
