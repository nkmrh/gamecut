"""動画の書き出し（下ごしらえ・本編・ショート）"""
from __future__ import annotations

from pathlib import Path

from . import ff


def _write_graph(workdir: Path, name: str, graph: str) -> Path:
    p = workdir / name
    p.write_text(graph, encoding="utf-8")
    return p


def keep_segments(silences: list[tuple[float, float]], duration: float, padding: float) -> list[tuple[float, float]]:
    """無音区間から「残す区間」を作る（前後に少し余白を残す）"""
    keep, cur = [], 0.0
    for a, b in silences:
        a2, b2 = a + padding, min(b, duration) - padding
        if b2 - a2 < 0.3:
            continue
        if a2 > cur:
            keep.append((cur, a2))
        cur = b2
    if cur < duration:
        keep.append((cur, duration))
    return [(a, b) for a, b in keep if b - a > 0.05]


def render_prep(src: Path, info: ff.Info, keep: list[tuple[float, float]] | None,
                out: Path, cfg: dict, workdir: Path) -> None:
    v = cfg["video"]
    W, H, fps = v["width"], v["height"], v["fps"]
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"setsar=1,fps={fps}")
    inputs = ["-i", src]
    if keep:
        expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
        graph = (f"[0:v]{vf},select='{expr}',setpts=N/FRAME_RATE/TB[v];"
                 f"[0:a]aresample=48000,aselect='{expr}',asetpts=N/SR/TB[a]")
    elif info.has_audio:
        graph = f"[0:v]{vf}[v];[0:a]aresample=48000[a]"
    else:
        inputs += ["-f", "lavfi", "-t", f"{info.duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph = f"[0:v]{vf}[v];[1:a]anull[a]"
    script = _write_graph(workdir, "prep.filter", graph)
    ff.run([*inputs, *ff.filter_script_args(script), "-map", "[v]", "-map", "[a]",
            *ff.encoder_args(cfg, intermediate=True), "-c:a", "aac", "-b:a", "256k", "-ac", "2", "-ar", "48000", out],
           desc="下ごしらえ（無音カット・サイズ統一）" if keep else "下ごしらえ（サイズ統一）")


def _audio_graph(voice: str, bgm_input: int | None, total: float, cfg: dict) -> str:
    """声と BGM を混ぜる。声があるときは BGM を自動で小さくする"""
    b = cfg["bgm"]
    if bgm_input is None:
        return f"[{voice}]alimiter=limit=0.95[a]"
    fade = min(b["fade_out"], total / 3)
    g = (f"[{bgm_input}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={b['volume']},"
         f"afade=t=out:st={max(0, total - fade):.3f}:d={fade:.3f},apad[bg];")
    if b["ducking"]:
        g += (f"[{voice}]asplit=2[vo][sc];"
              f"[bg][sc]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=600[bgd];"
              f"[vo][bgd]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    else:
        g += f"[{voice}][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
    return g


def render_main(prep: Path, duration: float, overlay: Path, bgm_wav: Path | None,
                outro_image: Path | None, out: Path, cfg: dict, workdir: Path) -> None:
    v = cfg["video"]
    W, H, fps = v["width"], v["height"], v["fps"]
    E = float(cfg["outro"]["duration"]) if cfg["outro"]["enabled"] else 0.0
    total = duration + E
    inputs = ["-i", prep, "-f", "concat", "-safe", "0", "-i", overlay]
    idx = 2
    bgm_idx = None
    if bgm_wav:
        inputs += ["-i", bgm_wav]
        bgm_idx, idx = idx, idx + 1
    g = "[0:v]"
    if E:
        g += f"tpad=stop_mode=clone:stop_duration={E:.3f},boxblur=20:2:enable='gte(t,{duration:.3f})'"
    else:
        g += "null"
    g += "[base];"
    base = "base"
    if E and outro_image:
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{total:.3f}", "-i", outro_image]
        g += (f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[img];"
              f"[base][img]overlay=enable='gte(t,{duration:.3f})'[base2];")
        base = "base2"
        idx += 1
    g += f"[{base}][1:v]overlay=0:0:eof_action=repeat,format=yuv420p[v];"
    g += f"[0:a]apad=pad_dur={E:.3f}[voice];" if E else "[0:a]anull[voice];"
    g += _audio_graph("voice", bgm_idx, total, cfg)
    script = _write_graph(workdir, "main.filter", g)
    ff.run([*inputs, *ff.filter_script_args(script), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            *ff.encoder_args(cfg), "-c:a", "aac", "-b:a", v["audio_bitrate"], out],
           desc="本編を書き出し中")


def render_short(prep: Path, start: float, dur: float, overlay: Path, bgm_wav: Path | None,
                 out: Path, cfg: dict, workdir: Path, name: str) -> None:
    W, H = 1080, 1920
    zoom = float(cfg["shorts"].get("zoom", 1.25))
    fw = int(W * zoom) // 2 * 2
    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", prep, "-f", "concat", "-safe", "0", "-i", overlay]
    bgm_idx = None
    if bgm_wav:
        inputs += ["-i", bgm_wav]
        bgm_idx = 2
    g = (f"[0:v]split=2[a][b];"
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:2[bg];"
         f"[b]scale={fw}:-2,crop=min(iw\\,{W}):ih[fg];"
         f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base];"
         f"[base][1:v]overlay=0:0:eof_action=repeat,format=yuv420p[v];"
         f"[0:a]afade=t=in:d=0.3,afade=t=out:st={max(0, dur - 0.6):.3f}:d=0.6[voice];")
    g += _audio_graph("voice", bgm_idx, dur, cfg)
    script = _write_graph(workdir, f"{name}.filter", g)
    ff.run([*inputs, *ff.filter_script_args(script), "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
            *ff.encoder_args(cfg), "-c:a", "aac", "-b:a", cfg["video"]["audio_bitrate"], out],
           desc=f"ショート {name} を書き出し中")
