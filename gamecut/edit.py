"""面白くなるようにカット編集する。

ゲーム実況は無音がほとんどないので、無音ではなく「面白さ」で採点して残す部分を選ぶ。
- 盛り上がり（声・ゲーム音の大きさ＋画面の動き）、しゃべっているか、リアクションの言葉で1秒ごとに採点
- 区切りはテロップ（文）の切れ目に合わせ、話の途中では切らない
- 盛り上がった瞬間はその数秒前（前振り）から必ず残す。冒頭の挨拶と締めも残す
- 残さない部分は「早送り」にし、長すぎるところは「中略」として削る
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from . import ff, render
from .config import parse_time
from .highlights import smooth

REACTION = re.compile(
    r"やば|ヤバ|うわ|ウワ|すご|すげ|スゲ|きた|キタ|来た|よっしゃ|しゃあ|うそ|嘘|まじ|マジ|待って|まって|無理|"
    r"えぐ|エグ|神|最高|勝った|負け|死ん|痛|危な|あぶな|なんで|なんだ|おお|ああ|ええ|草|笑|w{2,}|ｗ{2,}|[!！]"
)


# ---------------------------------------------------------------- 計画づくり
def _units(D: float, caps: list[dict], max_gap_unit: float = 8.0) -> list[list[float]]:
    """しゃべりのまとまり（短い間でつながる文）と、しゃべっていない区間を細かく分けた単位に分ける"""
    groups: list[list[float]] = []
    for c in caps:
        a, b = max(0.0, c["start"]), min(D, c["end"])
        if b <= a:
            continue
        if groups and a - groups[-1][1] < 0.6:
            groups[-1][1] = max(groups[-1][1], b)
        else:
            groups.append([a, b])
    units, cur = [], 0.0
    for a, b in groups + [[D, D]]:
        a = max(a, cur)
        if a - cur > 0.05:
            n = max(1, math.ceil((a - cur) / max_gap_unit))
            step = (a - cur) / n
            units += [[cur + i * step, cur + (i + 1) * step] for i in range(n)]
        if b > a:
            units.append([a, b])
        cur = max(cur, b)
    return units


def _forced(side: dict, key: str) -> list[tuple[float, float]]:
    return [(parse_time(r["start"]), parse_time(r["end"])) for r in side.get(key) or []]


def _overlaps(a: float, b: float, ranges: list[tuple[float, float]]) -> bool:
    return any(a < y and b > x for x, y in ranges)


def plan_highlight(D: float, score: np.ndarray, caps: list[dict], cfg: dict, side: dict) -> dict:
    e = cfg["edit"]
    n = max(1, math.ceil(D))
    sc = np.pad(score[:n], (0, max(0, n - len(score))), mode="edge") if len(score) else np.zeros(n)
    speech, react = np.zeros(n), np.zeros(n)
    for c in caps:
        s, t = int(c["start"]), min(n, math.ceil(c["end"]))
        speech[s:t] = 1
        if REACTION.search(c["text"]):
            react[s:t] = 1
    sm = smooth(sc, 3)
    value = 0.45 * sm + 0.35 * speech + 0.2 * react
    peak_thr = float(np.percentile(sm, 92)) if n > 10 else 2.0
    peaks = [i + 0.5 for i in range(1, n - 1) if sm[i] >= peak_thr and sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1]]
    # 近すぎるピークは1つにまとめる
    merged_peaks: list[float] = []
    for p in peaks:
        if merged_peaks and p - merged_peaks[-1] < 8:
            if sm[int(p)] > sm[int(merged_peaks[-1])]:
                merged_peaks[-1] = p
        else:
            merged_peaks.append(p)

    units = _units(D, caps)
    force_keep, force_cut = _forced(side, "keep"), _forced(side, "cut")
    pre = float(e["peak_preroll"])
    must, score_u = [], []
    for a, b in units:
        i, j = int(a), max(int(a) + 1, math.ceil(b))
        s = float(value[i:j].mean())
        has_peak = any(a - 1.5 <= p <= b + pre for p in merged_peaks)
        score_u.append(s + (0.3 if has_peak else 0))
        must.append(has_peak or a < e["keep_intro"] or b > D - e["keep_outro"]
                    or _overlaps(a, b, force_keep))
    cut = [_overlaps(a, b, force_cut) and not _overlaps(a, b, force_keep) for a, b in units]
    keep = [m and not c for m, c in zip(must, cut)]

    target = D * float(e["keep_ratio"])
    total = sum(b - a for (a, b), k in zip(units, keep) if k)
    for idx in sorted(range(len(units)), key=lambda k: -score_u[k]):
        if total >= target:
            break
        if not keep[idx] and not cut[idx]:
            keep[idx] = True
            total += units[idx][1] - units[idx][0]
    # 細かすぎるカットはテンポが悪くなるので、短い隙間は残す
    for idx in range(1, len(units) - 1):
        if not keep[idx] and not cut[idx] and keep[idx - 1] and keep[idx + 1] and \
                units[idx][1] - units[idx][0] < e["min_cut"]:
            keep[idx] = True

    segs: list[dict] = []
    idx = 0
    while idx < len(units):
        k = keep[idx]
        j = idx
        while j + 1 < len(units) and keep[j + 1] == k and cut[j + 1] == cut[idx]:
            j += 1
        a, b = units[idx][0], units[j][1]
        if k:
            segs.append({"start": a, "end": b, "kind": "keep", "speed": 1.0})
        elif e["fast_forward"] and not cut[idx] and b - a >= 4:
            out_len = min(float(e["fast_max_out"]), (b - a) / 2)
            speed = min(float(e["fast_max_speed"]), (b - a) / out_len)
            chunk = min(b - a, out_len * speed)
            segs.append({"start": a, "end": a + chunk, "kind": "fast", "speed": round(speed, 3),
                         "skipped": round((b - a) - chunk, 2)})
            if chunk < b - a:
                segs.append({"start": a + chunk, "end": b, "kind": "cut", "speed": 0})
        else:
            segs.append({"start": a, "end": b, "kind": "cut", "speed": 0})
        idx = j + 1
    return _finish(segs, merged_peaks, D, cfg)


def plan_silence(prep: Path, D: float, cfg: dict) -> dict:
    cs = cfg["cut_silence"]
    sil = ff.detect_silence(prep, cs["noise_db"], cs["min_silence"])
    keep = render.keep_segments(sil, D, cs["padding"])
    segs, cur = [], 0.0
    for a, b in keep:
        if a > cur:
            segs.append({"start": cur, "end": a, "kind": "cut", "speed": 0})
        segs.append({"start": a, "end": b, "kind": "keep", "speed": 1.0})
        cur = b
    if cur < D:
        segs.append({"start": cur, "end": D, "kind": "cut", "speed": 0})
    return _finish(segs, [], D, cfg)


def plan_none(D: float, cfg: dict) -> dict:
    return _finish([{"start": 0.0, "end": D, "kind": "keep", "speed": 1.0}], [], D, cfg)


def _finish(segs: list[dict], peaks: list[float], D: float, cfg: dict) -> dict:
    """フレーム単位にそろえ、書き出し後の時刻（out_start）を付ける"""
    fps = cfg["video"]["fps"]
    q = lambda t: round(round(t * fps) / fps, 4)  # noqa: E731
    out, t = [], 0.0
    for s in segs:
        a, b = q(s["start"]), q(s["end"])
        if b - a < 1 / fps:
            continue
        s = {**s, "start": a, "end": b}
        if s["kind"] != "cut":
            s["out_start"] = round(t, 4)
            t += (b - a) / s["speed"]
            s["out_end"] = round(t, 4)
        out.append(s)
    return {"source_duration": round(D, 3), "output_duration": round(t, 3),
            "peaks": [round(p, 2) for p in peaks], "segments": out}


# ---------------------------------------------------------------- 時刻の変換
def remap_time(t: float, plan: dict, nearest: bool = False) -> float | None:
    """元の時刻 → 編集後の時刻。カットされた所は None（nearest なら直後の残る位置）"""
    for s in plan["segments"]:
        if s["start"] <= t < s["end"]:
            if s["kind"] == "cut":
                break
            return s["out_start"] + (t - s["start"]) / s["speed"]
    if nearest:
        later = [s for s in plan["segments"] if s["kind"] != "cut" and s["start"] >= t]
        return later[0]["out_start"] if later else plan["output_duration"]
    return None


def remap_captions(caps: list[dict], plan: dict) -> list[dict]:
    """テロップを編集後の時刻に移す（早送りやカットされた部分のテロップは出さない）"""
    out = []
    for c in caps:
        for s in plan["segments"]:
            if s["kind"] != "keep":
                continue
            a, b = max(c["start"], s["start"]), min(c["end"], s["end"])
            if b - a >= 0.3:
                out.append({**c, "start": round(s["out_start"] + a - s["start"], 3),
                            "end": round(s["out_start"] + b - s["start"], 3)})
    return out


def output_peaks(plan: dict) -> list[float]:
    res = []
    for p in plan["peaks"]:
        t = remap_time(p, plan)
        seg = next((s for s in plan["segments"] if s["start"] <= p < s["end"]), None)
        if t is not None and seg and seg["kind"] == "keep":
            res.append(t)
    return res


def summary(plan: dict) -> str:
    def mmss(x):
        return f"{int(x // 60)}:{int(x % 60):02d}"
    cut = sum(s["end"] - s["start"] for s in plan["segments"] if s["kind"] == "cut")
    fast = sum(s["end"] - s["start"] for s in plan["segments"] if s["kind"] == "fast")
    return (f"{mmss(plan['source_duration'])} → {mmss(plan['output_duration'])}"
            f"（カット {mmss(cut)}／早送り {mmss(fast)}）")


# ---------------------------------------------------------------- 書き出し
def is_identity(plan: dict) -> bool:
    segs = plan["segments"]
    return len(segs) == 1 and segs[0]["kind"] == "keep"


def render_plan(prep: Path, plan: dict, out: Path, cfg: dict, work: Path) -> None:
    """区間ごとに書き出してつなぐ（音声は PCM にしてつなぎ目のズレを防ぐ）"""
    fps = cfg["video"]["fps"]
    segdir = work / "segments"
    segdir.mkdir(parents=True, exist_ok=True)
    parts = [s for s in plan["segments"] if s["kind"] != "cut"]
    names = []
    print(f"  ▶ カット編集を書き出し中（{len(parts)} 区間）", flush=True)
    for i, s in enumerate(parts):
        length = s["end"] - s["start"]
        if s["kind"] == "fast":
            vf = f"setpts=PTS/{s['speed']},fps={fps}"
            af = f"atrim=0:{length / s['speed']:.4f},volume=0"  # 早送り中の音は消して BGM に任せる
        else:
            vf, af = f"fps={fps}", "anull"
        name = f"seg_{i:05d}.mkv"
        ff.run(["-ss", f"{s['start']:.4f}", "-t", f"{length:.4f}", "-i", prep, "-vf", vf, "-af", af,
                *ff.encoder_args(cfg, intermediate=True), "-g", str(fps * 2),
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", segdir / name], quiet=True)
        names.append(name)
        print(f"\r    {i + 1}/{len(parts)}", end="", flush=True)
    print()
    lst = segdir / "list.ffconcat"
    lst.write_text("ffconcat version 1.0\n" + "".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    ff.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], quiet=True)
    for n in names:
        (segdir / n).unlink(missing_ok=True)


def load_or_plan(od: Path, make, cfg: dict) -> dict:
    """edit_plan.json があればそれを使う（手で直して再実行できるように）"""
    f = od / "edit_plan.json"
    if f.exists():
        print("  ▶ カット編集は edit_plan.json を使います（作り直すときは削除してください）")
        old = json.loads(f.read_text(encoding="utf-8"))
        # 手で直した場合に備えて、書き出し後の時刻を計算し直す
        return _finish(old["segments"], old.get("peaks", []), old["source_duration"], cfg)
    plan = make()
    f.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan
