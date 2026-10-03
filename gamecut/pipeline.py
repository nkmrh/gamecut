"""素材1本ごとの処理の流れ・分類・アップロード"""
from __future__ import annotations

import json
import shutil
import time
import traceback
from pathlib import Path

import numpy as np

from . import bgm, captions, ff, highlights, render, thumbnail, youtube
from .config import VIDEO_EXTS, fill, load_sidecar, parse_time, resolve, sidecar_path
from .text import Cue, Item, build_overlay, find_font


# ---------------------------------------------------------------- フォルダと状態
def output_root(folder: Path, cfg: dict) -> Path:
    return folder / cfg["folders"]["output"]


def done_root(folder: Path, cfg: dict) -> Path:
    return folder / cfg["folders"]["done"]


def list_sources(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("."))


def load_state(od: Path) -> dict:
    f = od / "state.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_state(od: Path, state: dict) -> None:
    (od / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while (p := path.with_name(f"{path.stem}_{i}{path.suffix}")).exists():
        i += 1
    return p


def classify_done(src: Path, folder: Path, cfg: dict) -> Path:
    """作成が終わった素材（と動画ごとの設定ファイル）を作成済みフォルダへ移す"""
    dest_dir = done_root(folder, cfg)
    dest_dir.mkdir(exist_ok=True)
    side = sidecar_path(src)
    dest = _unique(dest_dir / src.name)
    shutil.move(str(src), str(dest))
    if side:
        shutil.move(str(side), str(_unique(dest_dir / f"{dest.stem}{side.suffix}")))
    return dest


# ---------------------------------------------------------------- 文字レイヤー
def main_cues(title: str, caps: list[dict], D: float, cfg: dict) -> list[Cue]:
    cues = []
    t, c, o = cfg["title"], cfg["captions"], cfg["outro"]
    if t["enabled"] and title:
        cues.append(Cue(0, min(t["duration"], D), [Item(title, t["font_size"], 0.45, t["color"],
                                                        t["outline_color"], band=(0, 0, 0, 150))]))
    for cap in caps:
        if cap["start"] < D:
            cues.append(Cue(cap["start"], min(cap["end"], D), [Item(cap["text"], c["font_size"], 0.87,
                            c["color"], c["outline_color"], max_lines=c["max_lines"])]))
    if o["enabled"]:
        items = []
        if o["text"]:
            items.append(Item(o["text"], 96, 0.36))
        if o["sub_text"]:
            items.append(Item(o["sub_text"], 52, 0.52, max_lines=1))
        if o["button_text"]:
            items.append(Item(o["button_text"], 64, 0.70, "#FFFFFF", button="#FF0000", max_lines=1))
        cues.append(Cue(D, D + o["duration"], items))
    return cues


def short_cues(title: str, caps: list[dict], start: float, dur: float, cfg: dict) -> list[Cue]:
    c = cfg["captions"]
    cues = [Cue(0, dur, [Item(title, 76, 0.13, band=(0, 0, 0, 160))])]
    for cap in caps:
        a, b = cap["start"] - start, cap["end"] - start
        if b > 0 and a < dur:
            cues.append(Cue(max(0, a), min(dur, b), [Item(cap["text"], 66, 0.74, c["color"], c["outline_color"],
                                                          max_width=0.92, max_lines=c["max_lines"])]))
    end_text = cfg["shorts"]["end_text"]
    if end_text and dur > 6:
        cues.append(Cue(dur - 2.5, dur, [Item(end_text, 72, 0.88, "#FFE600", band=(0, 0, 0, 170), max_lines=1)]))
    return cues


# ---------------------------------------------------------------- 1本の処理
def process(src: Path, folder: Path, cfg: dict, *, shorts: bool = True) -> Path:
    stem = src.stem
    od = output_root(folder, cfg) / stem
    work = od / "_work"
    work.mkdir(parents=True, exist_ok=True)
    state = load_state(od)
    side = load_sidecar(src)
    title = str(side.get("title") or fill(cfg["title"]["template"], name=stem))
    font_path = find_font(cfg["font"]["path"])
    print(f"\n=== {src.name} → 「{title}」")

    # 1. 下ごしらえ（無音カット・サイズ統一）
    prep = work / "prep.mp4"
    if not (state.get("prep_done") and prep.exists()):
        info = ff.probe(src)
        keep = None
        cs = cfg["cut_silence"]
        if cs["enabled"] and info.has_audio:
            print("  ▶ 無音を検出中…", flush=True)
            sil = ff.detect_silence(src, cs["noise_db"], cs["min_silence"])
            keep = render.keep_segments(sil, info.duration, cs["padding"])
            kept = sum(b - a for a, b in keep)
            if kept >= info.duration * 0.98:
                keep = None
            else:
                print(f"  ・無音カット: {info.duration:.0f}秒 → {kept:.0f}秒")
        render.render_prep(src, info, keep, prep, cfg, work)
        state.update(prep_done=True, keep=keep or [(0.0, info.duration)], source_duration=info.duration)
        save_state(od, state)
    D = ff.probe(prep).duration
    keep = [tuple(k) for k in state["keep"]]

    # 2. テロップ
    caps = captions.load_or_transcribe(prep, od / "captions.json", cfg) if cfg["captions"]["enabled"] else []

    # 3. 解析（盛り上がり度）
    print("  ▶ 盛り上がりを解析中…", flush=True)
    score = highlights.excitement(ff.audio_envelope(prep), ff.motion_envelope(prep))
    E = float(cfg["outro"]["duration"]) if cfg["outro"]["enabled"] else 0.0
    title_dur = float(cfg["title"]["duration"]) if cfg["title"]["enabled"] else 0.0

    # 4. BGM（場面ごとに自動選曲）
    tracks = bgm.all_tracks(cfg) if cfg["bgm"]["enabled"] else []
    bgm_wav, sections = None, None
    if cfg["bgm"]["enabled"]:
        bgm_wav = work / "bgm_main.wav"
        sections = bgm.build(score, D + E, cfg, side, stem, work, bgm_wav, tracks)
        if sections is None:
            bgm_wav = None
        else:
            (od / "bgm_plan.json").write_text(json.dumps(
                [{k: s[k] for k in ("start", "end", "mood", "title")} for s in sections],
                ensure_ascii=False, indent=1), encoding="utf-8")

    # 5. 本編
    main_mp4 = od / "main.mp4"
    overlay = build_overlay(main_cues(title, caps, D, cfg), D + E,
                            (cfg["video"]["width"], cfg["video"]["height"]), work / "ov_main", font_path)
    outro_img = resolve(cfg, cfg["outro"]["image"]) if cfg["outro"]["image"] else None
    render.render_main(prep, D, overlay, bgm_wav, outro_img, main_mp4, cfg, work)

    # 6. サムネイル
    thumb = None
    if cfg["thumbnail"]["enabled"]:
        thumb = od / "thumbnail.jpg"
        if side.get("thumbnail_image"):
            frame = resolve(cfg, side["thumbnail_image"])
        else:
            if side.get("thumbnail_time") is not None:
                t = highlights.map_time(parse_time(side["thumbnail_time"]), keep)
            else:
                t = highlights.best_moment(score, title_dur + 1, 1)
            frame = work / "thumb_frame.png"
            ff.extract_frame(prep, min(t, max(0, D - 0.5)), frame)
        text = str(side.get("thumbnail_text") or fill(cfg["thumbnail"]["text"], title=title, name=stem))
        thumbnail.make(frame, text, thumb, cfg, font_path)
        print(f"  ✓ サムネイル: {thumb.name}")

    # 7. ショート
    shorts_info = []
    sc = cfg["shorts"]
    if shorts and sc["enabled"] and D >= 15:
        if side.get("shorts"):
            wins = [(highlights.map_time(parse_time(s["start"]), keep),
                     highlights.map_time(parse_time(s["end"]), keep), s.get("title")) for s in side["shorts"]]
        else:
            L = int(min(sc["duration"], D))
            wins = [(*highlights.snap(w, caps), None)
                    for w in highlights.pick_windows(score, L, sc["count"], skip_start=title_dur)]
        picker = bgm.Picker(tracks, stem + "_shorts") if tracks else None
        for i, (a, b, stitle) in enumerate(wins, 1):
            a, b = max(0.0, a), min(D, b)
            dur = min(b - a, 180.0)
            if dur < 5:
                continue
            name = f"short_{i:02d}"
            s_title = str(stitle or (f"{title}（その{i}）" if len(wins) > 1 else title))
            s_bgm, s_credit = None, ""
            if sc["bgm"] and picker:
                mood = side.get("bgm_mood") or bgm.level_of(float(np.mean(score[int(a):int(a + dur)] if
                                                                          int(a + dur) > int(a) else [0.5])))
                tr = picker.pick(mood)
                if tr:
                    s_bgm = work / f"bgm_{name}.wav"
                    bgm.render_track([{"start": 0, "end": dur, "mood": mood, "track": tr["path"],
                                       "title": tr["title"]}], work, s_bgm)
                    s_credit = tr["credit"]
            ov = build_overlay(short_cues(s_title, caps, a, dur, cfg), dur, (1080, 1920), work / f"ov_{name}",
                               font_path)
            out = od / f"{name}.mp4"
            render.render_short(prep, a, dur, ov, s_bgm, out, cfg, work, name)
            shorts_info.append({"file": out.name, "title": fill(sc["title_template"], title=s_title, name=stem),
                                "start": round(a, 2), "end": round(a + dur, 2),
                                "credit": s_credit})

    # 8. アップロード用の情報を記録
    y = cfg["youtube"]
    desc = str(side.get("description") or fill(y["description_template"], title=title, name=stem))
    desc = desc.rstrip() + bgm.credits(sections or [])
    tags = list(dict.fromkeys([*y["tags"], *side.get("tags", [])]))
    prev = state.get("upload", {})
    state["upload"] = {
        "main": {"file": main_mp4.name, "title": title, "description": desc, "tags": tags,
                 "privacy": side.get("privacy") or y["privacy"], "publish_at": side.get("publish_at"),
                 "thumbnail": thumb.name if thumb else None, "id": prev.get("main", {}).get("id")},
        "shorts": [{**s, "tags": [*tags, "Shorts"], "privacy": side.get("privacy") or y["shorts_privacy"],
                    "id": None} for s in shorts_info],
    }
    state.update(rendered=True, title=title, source=src.name)
    save_state(od, state)

    # 9. 素材を分類（作成済みへ移動）
    moved = classify_done(src, folder, cfg)
    state["source"] = str(moved.relative_to(folder))
    save_state(od, state)
    if not cfg["video"]["keep_intermediate"]:
        shutil.rmtree(work, ignore_errors=True)
    print(f"  ✓ 作成完了: {od}")
    print(f"  ✓ 素材を「{cfg['folders']['done']}」へ移動しました")
    return od


# ---------------------------------------------------------------- アップロード
def upload_one(od: Path, cfg: dict, service) -> None:
    state = load_state(od)
    up = state.get("upload")
    if not state.get("rendered") or not up:
        return
    y = cfg["youtube"]
    m = up["main"]
    if not m.get("id"):
        m["id"] = youtube.upload(service, od / m["file"], m["title"], m["description"], m["tags"], cfg,
                                 m["privacy"], m.get("publish_at"))
        save_state(od, state)
        if m.get("thumbnail"):
            youtube.set_thumbnail(service, m["id"], od / m["thumbnail"])
        actual = youtube.actual_privacy(service, m["id"])
        print(f"  ✓ 本編: https://youtu.be/{m['id']}（公開状態: {actual}）")
        if actual != m["privacy"] and not m.get("publish_at"):
            print("  ! 指定と違う公開状態になっています。審査前の API プロジェクトでは YouTube 側で"
                  "非公開に固定されます（README の「公開で投稿するには」を参照）")
    main_url = f"https://youtu.be/{m['id']}"
    for s in up["shorts"]:
        if s.get("id"):
            continue
        desc = fill(y["shorts_description_template"], title=m["title"], main_url=main_url)
        if s.get("credit"):
            desc += "\n\n♪ BGM\n" + s["credit"]
        s["id"] = youtube.upload(service, od / s["file"], s["title"], desc, s["tags"], cfg, s["privacy"])
        save_state(od, state)
        print(f"  ✓ ショート: https://youtube.com/shorts/{s['id']}")
    state["uploaded"] = True
    save_state(od, state)


def pending_uploads(folder: Path, cfg: dict) -> list[Path]:
    root = output_root(folder, cfg)
    if not root.exists():
        return []
    out = []
    for od in sorted(p for p in root.iterdir() if p.is_dir()):
        st = load_state(od)
        if st.get("rendered") and not st.get("uploaded"):
            out.append(od)
    return out


def upload_all(folder: Path, cfg: dict, targets: list[Path] | None = None) -> int:
    targets = pending_uploads(folder, cfg) if targets is None else targets
    if not targets:
        print("アップロード待ちの動画はありません。")
        return 0
    y = cfg["youtube"]
    try:
        service = youtube.get_service(resolve(cfg, y["client_secrets"]), resolve(cfg, y["token"]), interactive=True)
    except youtube.AuthError as e:
        print(f"\n! アップロードできません: {e}\n  作成した動画は「{cfg['folders']['output']}」に保存されています。"
              "準備ができたら「再アップロード」を実行してください。")
        return len(targets)
    failed = 0
    for od in targets:
        print(f"\n=== アップロード: {od.name}")
        try:
            upload_one(od, cfg, service)
        except Exception as e:
            failed += 1
            msg = str(e)
            if "quotaExceeded" in msg or "uploadLimitExceeded" in msg:
                print("  ! 今日のアップロード上限に達しました。明日「再アップロード」を実行してください。")
                break
            print(f"  ! アップロード失敗: {msg}\n    あとで「gamecut upload」でやり直せます。")
    return failed


# ---------------------------------------------------------------- まとめて実行
def run_all(folder: Path, cfg: dict, *, upload: bool, shorts: bool, only: str | None) -> int:
    sources = list_sources(folder)
    if only:
        sources = [s for s in sources if s.stem == only or s.name == only]
    if not sources:
        print(f"未作成の素材はありません（{folder}）。動画ファイルをこのフォルダに入れてください。")
    made, failed = [], 0
    for src in sources:
        if time.time() - src.stat().st_mtime < 60:
            print(f"\n--- {src.name} は録画中またはコピー中のようなので今回は飛ばします")
            continue
        try:
            made.append(process(src, folder, cfg, shorts=shorts))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            failed += 1
            print(f"  ! {src.name} の作成に失敗しました: {e}")
            traceback.print_exc()
    if upload and cfg["youtube"]["upload"]:
        pend = pending_uploads(folder, cfg)
        if pend:
            failed += upload_all(folder, cfg, pend)
    print(f"\n完了: 作成 {len(made)} 本 / 失敗 {failed} 件")
    return failed


def status(folder: Path, cfg: dict) -> None:
    src = list_sources(folder)
    print(f"■ 未作成の素材（{len(src)}本）")
    for s in src:
        print(f"   - {s.name}")
    root = output_root(folder, cfg)
    rows = []
    if root.exists():
        for od in sorted(p for p in root.iterdir() if p.is_dir()):
            st = load_state(od)
            if not st.get("rendered"):
                label = "作成途中"
            elif st.get("uploaded"):
                label = "アップロード済み"
            else:
                label = "作成済み・未アップロード"
            main_id = (st.get("upload") or {}).get("main", {}).get("id")
            rows.append(f"   - [{label}] {od.name}" + (f"  https://youtu.be/{main_id}" if main_id else ""))
    print(f"■ 作成した動画（{len(rows)}本）")
    print("\n".join(rows) if rows else "   なし")
