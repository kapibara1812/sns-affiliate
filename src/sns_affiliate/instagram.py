"""Instagram API (Instagram Login) による投稿・インサイト取得."""

from __future__ import annotations

import time

import requests

from .config import Settings


class InstagramError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None,
                 poll_interval: float = 5.0):
        if not settings.ig_user_id or not settings.ig_access_token:
            raise InstagramError("IG_USER_ID / IG_ACCESS_TOKEN が設定されていません")
        self.s = settings
        self.session = session or requests.Session()
        self.poll_interval = poll_interval

    def _request(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.s.ig_access_token
        url = f"{self.s.ig_api_base}/{path.lstrip('/')}"
        resp = self.session.request(method, url, params=params, timeout=30)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in data:
            raise InstagramError(f"{method} {path} failed: {data.get('error', resp.text)}")
        return data

    def publish_image(self, image_url: str, caption: str) -> dict:
        """画像1枚を投稿し {id, permalink} を返す."""
        container = self._request("POST", f"{self.s.ig_user_id}/media",
                                  image_url=image_url, caption=caption)
        container_id = container["id"]

        for _ in range(60):  # 最大5分待つ
            status = self._request("GET", container_id, fields="status_code,status")
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in {"ERROR", "EXPIRED"}:
                raise InstagramError(f"コンテナ処理に失敗: {status}")
            time.sleep(self.poll_interval)
        else:
            raise InstagramError("コンテナ処理がタイムアウトしました")

        media = self._request("POST", f"{self.s.ig_user_id}/media_publish", creation_id=container_id)
        media_id = media["id"]
        info = self._request("GET", media_id, fields="permalink,timestamp")
        return {"id": media_id, "permalink": info.get("permalink", ""), "timestamp": info.get("timestamp")}

    def insights(self, media_id: str) -> dict[str, int]:
        data = self._request("GET", f"{media_id}/insights", metric=self.s.ig_insight_metrics)
        result = {}
        for metric in data.get("data", []):
            values = metric.get("values") or [{}]
            result[metric["name"]] = values[0].get("value", metric.get("total_value", {}).get("value", 0))
        return result

    def refresh_token(self) -> dict:
        """長期アクセストークン（60日）を更新する。発行から24時間以上経過している必要あり."""
        resp = self.session.get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": self.s.ig_access_token},
            timeout=30,
        )
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            raise InstagramError(f"トークン更新に失敗: {data}")
        return data
