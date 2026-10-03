"""Whisper（faster-whisper）による自動文字起こしとテロップの区切り"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import ff, setup

os.environ.setdefault("HF_HUB_VERBOSITY", "error")

# 無音部分で Whisper が出しがちな決まり文句
_HALLUCINATIONS = ("ご視聴ありがとうございました", "チャンネル登録をお願いします", "字幕は",
                   "お疲れ様でした", "ご清聴ありがとうございました")

_MODEL: dict = {}
_CUDA_ERRORS = ("cublas", "cudnn", "cuda", "cudart", "nvrtc")


def _is_cuda_error(e: Exception) -> bool:
    return any(k in str(e).lower() for k in _CUDA_ERRORS)


def _cuda_device_count() -> int:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def _register_cuda_dlls() -> bool:
    """pip で入れた NVIDIA のライブラリ（cuBLAS・cuDNN）を見つけられるようにする"""
    found = False
    for mod in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            pkg = __import__(mod, fromlist=["_"])
        except ImportError:
            return False
        for base in getattr(pkg, "__path__", []):
            for sub in ("bin", "lib"):
                d = Path(base) / sub
                if d.is_dir():
                    if os.name == "nt":
                        os.add_dll_directory(str(d))
                        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                    else:
                        os.environ["LD_LIBRARY_PATH"] = str(d) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
                    found = True
    return found


def _ensure_cuda_libs() -> bool:
    """NVIDIA の GPU があるとき、CUDA 12 用のライブラリがなければ自動で入れる"""
    if _register_cuda_dlls():
        return True
    if not setup.AUTO_INSTALL:
        return False
    print("  ▶ NVIDIA の GPU が見つかったので、GPU 用のライブラリを入れます（初回のみ・約1.5GB）", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
           "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]
    if subprocess.call(cmd) != 0:
        return False
    import importlib
    importlib.invalidate_caches()
    return _register_cuda_dlls()


def _load(name: str, device: str):
    from faster_whisper import WhisperModel
    compute = "int8_float16" if device == "cuda" else "int8"
    model = WhisperModel(name, device=device, compute_type=compute)
    if device == "cuda":
        # CUDA のライブラリは実際に計算するときに読み込まれるので、ここで1回試しておく
        segs, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="ja", vad_filter=False)
        list(segs)
    return model


def _model(name: str, want: str = "auto"):
    key = (name, want)
    if key in _MODEL:
        return _MODEL[key]
    print(f"  ▶ 文字起こしモデルを読み込み中（{name}。初回はダウンロードします）", flush=True)
    device = "cpu"
    if want in ("auto", "cuda") and _cuda_device_count() > 0 and _ensure_cuda_libs():
        device = "cuda"
    try:
        model = _load(name, device)
        if device == "cuda":
            print("  ・GPU（CUDA）で文字起こしします")
    except Exception as e:
        if device != "cuda" or not _is_cuda_error(e):
            raise
        print(f"  ! GPU を使えなかったので CPU で文字起こしします（{str(e).splitlines()[0][:120]}）")
        model = _load(name, "cpu")
    _MODEL[key] = model
    return model


def _fallback_to_cpu(name: str, want: str):
    """処理の途中で GPU のエラーが出たときに CPU のモデルに切り替える"""
    from faster_whisper import WhisperModel
    model = WhisperModel(name, device="cpu", compute_type="int8")
    _MODEL[(name, want)] = model
    return model


def transcribe(video: Path, cfg: dict) -> list[dict]:
    c = cfg["captions"]
    max_chars = c["max_chars_per_line"] * c["max_lines"]
    want = str(c.get("device", "auto"))
    model = _model(c["model"], want)
    print("  ▶ 文字起こし中…", flush=True)
    # 音声は ffmpeg で 16kHz モノラルに変換して渡す（PyAV のバージョン差の影響を受けないため）
    raw = subprocess.run([ff.tools()[0], "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    opts = dict(language=c["language"] or None, vad_filter=True, word_timestamps=True,
                condition_on_previous_text=False)
    try:
        segments = list(model.transcribe(audio, **opts)[0])
    except Exception as e:
        if not _is_cuda_error(e):
            raise
        print("  ! GPU でエラーが出たので CPU でやり直します")
        segments = list(_fallback_to_cpu(c["model"], want).transcribe(audio, **opts)[0])
    cues: list[dict] = []
    for seg in segments:
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            continue
        if any(h in seg.text for h in _HALLUCINATIONS) and seg.end - seg.start < 4:
            continue
        words = seg.words or []
        if not words:
            cues.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
            continue
        cur, start, last_end = "", words[0].start, words[0].start
        for w in words:
            gap = w.start - last_end
            if cur and (len(cur + w.word) > max_chars or gap > 0.8):
                cues.append({"start": start, "end": last_end, "text": cur.strip()})
                cur, start = "", w.start
            cur += w.word
            last_end = w.end
        if cur.strip():
            cues.append({"start": start, "end": last_end, "text": cur.strip()})
    # 1文字だけの切れ端は近くのテロップにくっつけるか捨てる
    merged: list[dict] = []
    for cue in cues:
        if merged and (len(merged[-1]["text"]) <= 1 or len(cue["text"]) <= 1) and cue["start"] - merged[-1]["end"] < 1.5:
            merged[-1]["text"] += cue["text"]
            merged[-1]["end"] = cue["end"]
        else:
            merged.append(cue)
    cues = [c for c in merged if len(c["text"].strip()) > 1]
    # 表示時間を整える（最低0.8秒、次のテロップに重ならない）
    for i, cue in enumerate(cues):
        end = max(cue["end"] + 0.2, cue["start"] + 0.8)
        if i + 1 < len(cues):
            end = min(end, cues[i + 1]["start"])
        cue["start"], cue["end"] = round(cue["start"], 2), round(max(end, cue["start"] + 0.3), 2)
    return [c for c in cues if c["text"]]


def load_or_transcribe(video: Path, out_json: Path, cfg: dict) -> list[dict]:
    if out_json.exists():
        print("  ▶ テロップは captions.json を使います（作り直すときは削除してください）")
        return json.loads(out_json.read_text(encoding="utf-8"))
    cues = transcribe(video, cfg)
    out_json.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    return cues
