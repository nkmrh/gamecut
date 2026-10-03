"""YouTube へのアップロード（YouTube Data API v3）"""
from __future__ import annotations

import random
import time
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]


class AuthError(RuntimeError):
    pass


def get_service(secrets: Path, token: Path, interactive: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise AuthError("YouTube にログインしていません。先に「gamecut auth」を実行してください。")
        if not secrets.exists():
            raise AuthError(f"{secrets} がありません。README の手順で Google Cloud から client_secret.json を"
                            "ダウンロードして、素材フォルダに置いてください。")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        print("ブラウザが開くので、アップロード先のチャンネルの Google アカウントでログインしてください。")
        creds = flow.run_local_server(port=0, prompt="consent")
    token.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _retryable(e: Exception) -> bool:
    from googleapiclient.errors import HttpError
    if isinstance(e, HttpError):
        return e.resp.status in (500, 502, 503, 504)
    return isinstance(e, (OSError, TimeoutError))


def upload(service, path: Path, title: str, description: str, tags: list[str], cfg: dict,
           privacy: str, publish_at: str | None = None) -> str:
    from googleapiclient.http import MediaFileUpload

    y = cfg["youtube"]
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags,
            "categoryId": str(y["category_id"]),
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(y["made_for_kids"])},
    }
    if publish_at:  # 予約投稿は非公開で上げて公開日時を指定する
        body["status"].update(privacyStatus="private", publishAt=publish_at)
    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = service.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, tries = None, 0
    print(f"  ▶ アップロード中: {title}")
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                print(f"\r    {int(status.progress() * 100):3d}%", end="", flush=True)
            tries = 0
        except Exception as e:
            tries += 1
            if not _retryable(e) or tries > 8:
                raise
            wait = min(60, 2 ** tries) + random.random()
            print(f"\n    通信エラーのため {wait:.0f} 秒後に再開します（{tries}回目）")
            time.sleep(wait)
    print("\r    100%")
    return resp["id"]


def set_thumbnail(service, video_id: str, jpg: Path) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    try:
        service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(jpg), mimetype="image/jpeg")).execute()
    except HttpError as e:
        if e.resp.status == 403:
            print("  ! サムネイルを設定できませんでした。チャンネルの電話番号確認が必要です"
                  "（https://www.youtube.com/verify）")
        else:
            raise


def actual_privacy(service, video_id: str) -> str:
    r = service.videos().list(part="status", id=video_id).execute()
    items = r.get("items") or []
    return items[0]["status"]["privacyStatus"] if items else "unknown"
