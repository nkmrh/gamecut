"""盛り上がり区間の選び方と、元の素材の時刻 → カット後の時刻の変換"""
from __future__ import annotations

import numpy as np


def map_time(t: float, keep: list[tuple[float, float]]) -> float:
    """元の素材の時刻 t を、無音カット後の時刻に変換する"""
    acc = 0.0
    for a, b in keep:
        if t < a:
            return acc
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def excitement(loud_db: np.ndarray, motion: np.ndarray) -> np.ndarray:
    """1秒ごとの盛り上がり度（0〜1）。音量と画面の動きを半々で合わせる"""
    n = max(len(loud_db), len(motion))

    def norm(x):
        x = np.pad(np.asarray(x, dtype=np.float32), (0, n - len(x)), mode="edge")
        lo, hi = np.percentile(x, 5), np.percentile(x, 95)
        return np.clip((x - lo) / (hi - lo + 1e-6), 0, 1)

    return 0.6 * norm(loud_db) + 0.4 * norm(motion)


def smooth(x: np.ndarray, window: int) -> np.ndarray:
    window = max(1, min(window, len(x)))
    return np.convolve(x, np.ones(window) / window, mode="same")


def pick_windows(score: np.ndarray, length: int, count: int, skip_start: float = 0,
                 skip_end: float = 0) -> list[tuple[float, float]]:
    """盛り上がり度の平均が高い、重ならない区間を count 個選ぶ"""
    n = len(score)
    if n == 0 or count <= 0:
        return []
    if n <= length:
        return [(0.0, float(n))]
    csum = np.concatenate([[0], np.cumsum(score)])
    means = (csum[length:] - csum[:-length]) / length  # means[i] = 区間 [i, i+length)
    valid = np.ones(len(means), dtype=bool)
    valid[: int(skip_start)] = False
    if skip_end:
        valid[max(0, len(means) - int(skip_end)):] = False
    picked = []
    for _ in range(count):
        if not valid.any():
            break
        i = int(np.argmax(np.where(valid, means, -np.inf)))
        picked.append((float(i), float(i + length)))
        valid[max(0, i - length): i + length] = False
    return sorted(picked)


def snap(win: tuple[float, float], captions: list[dict], limit: float = 3.0) -> tuple[float, float]:
    """区間の端をテロップの切れ目に合わせて、話の途中で切れないようにする"""
    a, b = win
    starts = [c["start"] for c in captions if a - limit <= c["start"] <= a + limit]
    ends = [c["end"] for c in captions if b - limit <= c["end"] <= b + limit]
    if starts:
        a = min(starts, key=lambda s: abs(s - a))
    if ends:
        b = min(ends, key=lambda e: abs(e - b))
    # 話の途中で始まるときは、その文の頭まで戻す
    for c in captions:
        if c["start"] < a < c["end"] and a - c["start"] <= limit:
            a = c["start"]
    return max(0.0, a - 0.3), b + 0.3


def best_moment(score: np.ndarray, skip_start: float, skip_end: float) -> float:
    n = len(score)
    s = smooth(score, 3).copy()
    s[: int(min(skip_start, n - 1))] = -1
    if skip_end and n > skip_end:
        s[n - int(skip_end):] = -1
    return float(np.argmax(s)) + 0.5
