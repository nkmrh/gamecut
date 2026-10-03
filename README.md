# gamecut — ゲーム実況動画の自動編集・YouTube アップロード

素材フォルダに録画を入れて実行するだけで、次の処理を自動で行います。Mac と Windows で動きます。

| 処理 | 内容 |
|---|---|
| 無音カット | 長い無音を自動で詰める |
| タイトル | 冒頭に動画タイトルを表示する |
| テロップ | Whisper で自動文字起こしして字幕を焼き込む |
| BGM | 盛り上がりを解析し、場面に合う曲（まったり・ふつう・激しい）へ自動で切り替える。声が入ると BGM を下げる |
| エンディング | 最後のコマを止めてぼかし、「チャンネル登録よろしくお願いします！」とボタンを表示する |
| サムネイル | 一番盛り上がった瞬間のコマに大きな文字を入れる |
| ショート | 盛り上がった場面から縦長のショートを作る（初期設定は2本） |
| アップロード | 本編・サムネ・ショートを YouTube に投稿する。ショートの説明文には本編リンクを入れる |
| 分類 | 作り終わった素材を `作成済み/` へ移す |

## かんたんな使い方（ダブルクリック）

1. このフォルダをデスクトップなど好きな場所に置く
2. Mac は `ゲーム実況編集.command`、Windows は `ゲーム実況編集.bat` をダブルクリックする
   - 初回は Python・ffmpeg・ライブラリ・BGM が自動で入ります（10〜20分）
   - デスクトップに `ゲーム実況素材` フォルダが作られます
3. `ゲーム実況素材` に録画を入れ、メニューで「1」を選ぶ

Windows では、別のフォルダをアイコンにドラッグ＆ドロップすると、そのフォルダを対象に処理できます。

## コマンドで使う

```
./gamecut.sh run  <フォルダ>               # 編集してアップロード（Windows は gamecut.bat）
./gamecut.sh run  <フォルダ> --no-upload   # 編集だけ
./gamecut.sh run  <フォルダ> --no-shorts   # ショートを作らない
./gamecut.sh run  <フォルダ> --only 動画名  # 1本だけ処理
./gamecut.sh upload <フォルダ>             # アップロード待ち・失敗分をアップロード
./gamecut.sh status <フォルダ>             # 未作成／作成済み／アップロード済みの一覧
./gamecut.sh auth <フォルダ>               # YouTube にログイン
./gamecut.sh init <フォルダ>               # 設定ファイルを作成
./gamecut.sh doctor [--fix]                # 必要なものの確認（--fix で自動インストール）
```

`--no-auto-install` を付けると、自動インストールをしません。

## フォルダの中身

```
ゲーム実況素材/
  gamecut.yaml          設定
  client_secret.json    YouTube 用の鍵（自分で置く）
  token.json            ログイン情報（自動で作られる）
  bgm/                  自分の BGM を入れると候補に加わる（bgm/激しい/ のように分けても可）
  プレイ1.mp4            ← 未作成の素材
  プレイ1.yaml           （任意）この動画だけの設定
  作成済み/              ← 作り終わった素材
  出力/プレイ1/          main.mp4, short_01.mp4, thumbnail.jpg, captions.json, bgm_plan.json, state.json
```

- テロップの誤字は `出力/<動画名>/captions.json` を直して、素材を元の場所に戻して再実行すれば反映されます。
- 途中で止まっても、もう一度実行すれば続きから再開します。

## 動画ごとの設定（`<動画名>.yaml`）

```yaml
title: "初見でボス戦に挑んだ結果"
description: "今回はボス戦です！"
tags: [ゲーム実況, 初見プレイ]
thumbnail_text: "まさかの結末"
thumbnail_time: "12:34"         # 元の素材の時刻のコマをサムネにする
thumbnail_image: "サムネ.png"    # 画像をそのまま使う場合
bgm_mood: 激しい                 # まったり / ふつう / 激しい で固定
bgm_file: "~/Music/曲.mp3"       # この曲だけを使う
shorts:                          # ショートにする場面を手動で決める（元の素材の時刻）
  - {start: "3:10", end: "3:50", title: "神回避"}
privacy: unlisted                # public / unlisted / private
publish_at: "2026-10-10T19:00:00+09:00"   # 予約投稿
```

## YouTube の準備（最初の1回だけ）

1. https://console.cloud.google.com/ を開き、プロジェクトを作成する
2. 「API とサービス」→「ライブラリ」で **YouTube Data API v3** を有効にする
3. 「Google Auth Platform」（OAuth 同意画面）を設定する
   - 対象は「外部」、テストユーザーに自分の Google アカウントを追加する
   - **「アプリを公開」で本番環境にする**。テストのままだと7日ごとに再ログインが必要になります
   - ログイン時に「Google はこのアプリを確認していません」と出たら、「詳細」→「（安全ではないページ）に移動」で進めます
4. 「認証情報」→「OAuth クライアント ID を作成」→ 種類は **デスクトップ アプリ** → JSON をダウンロードする
5. ダウンロードしたファイルを `client_secret.json` という名前にして素材フォルダに置く
6. メニューの「4」（または `gamecut auth`）でログインする

### 公開で投稿するには
Google の審査を受けていない API プロジェクトからアップロードした動画は、YouTube 側で**非公開に固定**されます。そうなったときは、アップロード後に警告が表示されます。

公開で投稿するには、[YouTube API 監査フォーム](https://support.google.com/youtube/contact/yt_api_form) から審査を申請してください。それまでは、YouTube Studio で手動で公開に切り替えます。

### 上限
- アップロードは1日あたり約6本までです（API の上限 10,000 ユニットに対し、1本 1,600 ユニット）。本編1本とショート2本で3本分を使います。
- 上限に達したときは、翌日「再アップロード」を実行してください。
- 自分で設定したサムネイルを使うには、チャンネルの電話番号確認（https://www.youtube.com/verify）が必要です。

## BGM について
- 初回に [incompetech.com](https://incompetech.com/)（Kevin MacLeod）から、曲調ごとに10曲ずつ自動でダウンロードします。保存先は `~/.gamecut/bgm` です。
- ライセンスは CC BY 4.0（収益化可）です。**使った曲のクレジットは説明文に自動で入ります。**
- `bgm/` に自分の曲を入れると、選曲の候補に加わります。曲調のフォルダに分けずに入れた曲は、自動で解析して振り分けます。
- 区間ごとに選ばれた曲は `出力/<動画名>/bgm_plan.json` で確認できます。

## 主な設定（`gamecut.yaml`）
書いていない項目には初期値が使われます。すべての項目は `gamecut/config.py` の `DEFAULTS` にあります。

| 項目 | 初期値 | 説明 |
|---|---|---|
| `captions.model` | small | 文字起こしの精度（tiny → large-v3 の順に正確だが遅い） |
| `cut_silence.noise_db` / `min_silence` | -35 / 1.5 | 無音とみなす音量と長さ |
| `bgm.volume` | 0.2 | BGM の音量 |
| `bgm.min_section` | 40 | BGM を切り替える最短の秒数 |
| `shorts.count` / `duration` | 2 / 45 | ショートの本数と長さ |
| `outro.image` | なし | エンディングに使う画像 |
| `video.encoder` | auto | auto（Mac は VideoToolbox、NVIDIA GPU は NVENC）/ libx264 |
| `font.path` | 自動 | 文字に使うフォント（Mac はヒラギノ、Windows は游ゴシック） |
| `youtube.privacy` | public | 公開設定 |

## 自動インストールについて
- **Python**：Mac は Homebrew か python.org、Windows は winget か python.org から入れます。専用環境は `~/.gamecut/venv` に作ります。
- **ffmpeg**：Mac は Homebrew、Windows は winget で入れます。どちらも使えないときは、単体版を `~/.gamecut/bin` にダウンロードします（管理者権限は不要）。
- **Whisper のモデル**：初回に自動でダウンロードします（small は約500MB）。

## 配布用 zip の作り方
```
python3 make_dist.py
```
`dist/gamecut.zip` ができます。中に鍵ファイル（client_secret.json・token.json）は入りません。
