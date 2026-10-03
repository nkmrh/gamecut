"""Whisper（faster-whisper）による自動文字起こしとテロップの区切り"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np

from . import ff

os.environ.setdefault("HF_HUB_VERBOSITY", "error")

# 無音部分で Whisper が出しがちな決まり文句
_HALLUCINATIONS = ("ご視聴ありがとうございました", "チャンネル登録をお願いします", "字幕は",
                   "お疲れ様でした", "ご清聴ありがとうございました")

_MODEL = {}


def _model(name: str):
    if name not in _MODEL:
        from faster_whisper import WhisperModel
        print(f"  ▶ 文字起こしモデルを読み込み中（{name}。初回はダウンロードします）", flush=True)
        _MODEL[name] = WhisperModel(name, device="auto", compute_type="int8")
    return _MODEL[name]


def transcribe(video: Path, cfg: dict) -> list[dict]:
    c = cfg["captions"]
    max_chars = c["max_chars_per_line"] * c["max_lines"]
    model = _model(c["model"])
    print("  ▶ 文字起こし中…", flush=True)
    # 音声は ffmpeg で 16kHz モノラルに変換して渡す（PyAV のバージョン差の影響を受けないため）
    raw = subprocess.run([ff.tools()[0], "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    segments, _ = model.transcribe(audio, language=c["language"] or None, vad_filter=True,
                                   word_timestamps=True, condition_on_previous_text=False)
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
