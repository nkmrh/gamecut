"""設定ファイル（gamecut.yaml）と動画ごとの設定（<動画名>.yaml）の読み込み"""
from __future__ import annotations

import copy
import datetime as _dt
from pathlib import Path

import yaml

CONFIG_NAME = "gamecut.yaml"
HOME_DIR = Path.home() / ".gamecut"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".flv", ".ts", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}

DEFAULTS: dict = {
    "folders": {"done": "作成済み", "output": "出力"},
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "encoder": "auto",  # auto / libx264 / h264_videotoolbox / h264_nvenc
        "crf": 20,
        "preset": "medium",
        "bitrate": "12M",  # ハードウェアエンコーダー用
        "audio_bitrate": "192k",
        "keep_intermediate": False,
    },
    "cut_silence": {"enabled": True, "noise_db": -35, "min_silence": 1.5, "padding": 0.3},
    "font": {"path": ""},
    "title": {
        "enabled": True,
        "template": "{name}",
        "duration": 4,
        "font_size": 110,
        "color": "#FFFFFF",
        "outline_color": "#000000",
    },
    "captions": {
        "enabled": True,
        "model": "small",
        "language": "ja",
        "font_size": 64,
        "color": "#FFFFFF",
        "outline_color": "#000000",
        "max_chars_per_line": 22,
        "max_lines": 2,
    },
    "bgm": {
        "enabled": True,
        "auto_download": True,
        "tracks_per_mood": 10,
        "folder": "bgm",
        "volume": 0.2,
        "ducking": True,
        "fade_out": 3,
        "min_section": 40,
    },
    "outro": {
        "enabled": True,
        "duration": 6,
        "text": "チャンネル登録よろしくお願いします！",
        "sub_text": "高評価・コメントもお待ちしています",
        "button_text": "チャンネル登録",
        "image": "",
    },
    "thumbnail": {
        "enabled": True,
        "text": "{title}",
        "color": "#FFE600",
        "outline_color": "#000000",
        "max_font_size": 170,
    },
    "shorts": {
        "enabled": True,
        "count": 2,
        "duration": 45,
        "title_template": "{title} #Shorts",
        "end_text": "フル動画はチャンネルで！",
        "bgm": True,
    },
    "youtube": {
        "upload": True,
        "client_secrets": "client_secret.json",
        "token": "token.json",
        "privacy": "public",
        "shorts_privacy": "public",
        "category_id": "20",
        "tags": ["ゲーム実況"],
        "made_for_kids": False,
        "description_template": "{title}\n\n#ゲーム実況",
        "shorts_description_template": "フル動画はこちら → {main_url}\n\n#Shorts #ゲーム実況",
    },
}

CONFIG_TEMPLATE = """\
# gamecut の設定ファイル
# 書いていない項目は初期値が使われます。

folders:
  done: 作成済み        # 作成が終わった素材の移動先
  output: 出力          # 出来上がった動画の保存先

title:
  template: "{name}"   # {name}=ファイル名 {date}=今日の日付。動画ごとの .yaml で title: を書くとそちらが優先
  duration: 4          # 冒頭にタイトルを出す秒数

cut_silence:
  enabled: true        # 長い無音を自動で詰める
  noise_db: -35        # これより小さい音を無音とみなす
  min_silence: 1.5     # この秒数以上続く無音を詰める

captions:
  enabled: true        # 自動でテロップを付ける
  model: small         # tiny / base / small / medium / large-v3（大きいほど正確で遅い）

bgm:
  enabled: true
  volume: 0.2          # BGM の音量（0〜1）

outro:
  duration: 6
  text: "チャンネル登録よろしくお願いします！"
  sub_text: "高評価・コメントもお待ちしています"

shorts:
  enabled: true
  count: 2             # 1本の素材から作るショートの本数
  duration: 45         # ショートの長さ（秒、最大60推奨）

youtube:
  upload: true
  privacy: public      # public / unlisted / private
  shorts_privacy: public
  tags: [ゲーム実況]
  description_template: |
    {title}

    #ゲーム実況
"""

SIDECAR_EXAMPLE = """\
# 動画ごとの設定の例（素材と同じ名前で .yaml を作ると、その動画だけに適用されます）
# title: "初見でボス戦に挑んだ結果"
# description: "今回はボス戦です！"
# tags: [ゲーム実況, 初見プレイ]
# thumbnail_text: "まさかの結末"
# thumbnail_time: "12:34"        # 元の素材の時刻
# bgm_mood: 激しい                # 激しい / ふつう / まったり で固定
# shorts:
#   - {start: "3:10", end: "3:50", title: "神回避"}
# privacy: unlisted
"""


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(folder: Path, config_path: Path | None = None) -> dict:
    path = config_path or folder / CONFIG_NAME
    user = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    cfg = deep_merge(DEFAULTS, user)
    cfg["_base_dir"] = str(path.parent if path.exists() else folder)
    return cfg


def resolve(cfg: dict, p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else Path(cfg["_base_dir"]) / path


def load_sidecar(video: Path) -> dict:
    side = video.with_suffix(".yaml")
    if not side.exists():
        side = video.with_suffix(".yml")
    if not side.exists():
        return {}
    with open(side, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def sidecar_path(video: Path) -> Path | None:
    for ext in (".yaml", ".yml"):
        p = video.with_suffix(ext)
        if p.exists():
            return p
    return None


def fill(template: str, **values) -> str:
    values.setdefault("date", _dt.date.today().strftime("%Y-%m-%d"))

    class _Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return template.format_map(_Safe(values))


def parse_time(value) -> float:
    """'1:23' / '01:02:03' / 83 を秒に変換"""
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(x) for x in str(value).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec
