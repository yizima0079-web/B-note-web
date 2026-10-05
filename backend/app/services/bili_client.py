"""B 站 API 客户端：二维码登录、用户信息、收藏夹与视频详情。"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .wbi import enc_wbi

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
PASSPORT = "https://passport.bilibili.com"
API = "https://api.bilibili.com"


class BiliError(RuntimeError):
    pass


class BiliClient:
    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self.cookies = cookies or {}
        self._wbi_keys: tuple[str, str] | None = None

    def _client(self) -> httpx.AsyncClient:
        headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
        return httpx.AsyncClient(headers=headers, cookies=self.cookies, timeout=20)

    # ── 登录 ────────────────────────────────────────────────
    @staticmethod
    async def generate_qr() -> dict[str, str]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as c:
            r = await c.get(f"{PASSPORT}/x/passport-login/web/qrcode/generate")
            data = r.json()
        if data.get("code") != 0:
            raise BiliError(f"获取二维码失败：{data.get('message')}")
        d = data["data"]
        return {"url": d["url"], "qrcode_key": d["qrcode_key"]}

    @staticmethod
    async def poll_qr(qrcode_key: str) -> dict[str, Any]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as c:
            r = await c.get(
                f"{PASSPORT}/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": qrcode_key},
            )
            data = r.json()
            cookies = {k: v for k, v in c.cookies.items()}
        d = data.get("data", {})
        # code: 0 成功, 86038 过期, 86090 已扫码待确认, 86101 未扫码
        return {"code": d.get("code"), "message": d.get("message"), "cookies": cookies}

    # ── 用户与收藏夹 ────────────────────────────────────────
    async def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys:
            return self._wbi_keys
        async with self._client() as c:
            r = await c.get(f"{API}/x/web-interface/nav")
            data = r.json()
        wbi = data["data"]["wbi_img"]
        img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
        sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        self._wbi_keys = (img_key, sub_key)
        return self._wbi_keys

    async def get_self_info(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"{API}/x/web-interface/nav")
            data = r.json()
        if data.get("code") != 0 or not data["data"].get("isLogin"):
            raise BiliError("B 站凭证无效或未登录")
        d = data["data"]
        return {"mid": str(d["mid"]), "uname": d["uname"], "face": d.get("face", "")}

    async def list_folders(self, mid: str) -> list[dict[str, Any]]:
        async with self._client() as c:
            r = await c.get(
                f"{API}/x/v3/fav/folder/created/list-all",
                params={"up_mid": mid},
            )
            data = r.json()
        if data.get("code") != 0:
            raise BiliError(f"获取收藏夹失败：{data.get('message')}")
        lst = (data.get("data") or {}).get("list") or []
        return [
            {
                "media_id": f["id"],
                "title": f["title"],
                "count": f["media_count"],
                "cover": f.get("cover", ""),
            }
            for f in lst
        ]

    async def list_resources(self, media_id: int, pn: int = 1, ps: int = 20) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(
                f"{API}/x/v3/fav/resource/list",
                params={"media_id": media_id, "pn": pn, "ps": ps, "platform": "web"},
            )
            data = r.json()
        if data.get("code") != 0:
            raise BiliError(f"获取收藏内容失败：{data.get('message')}")
        d = data.get("data") or {}
        medias = d.get("medias") or []
        items = [
            {
                "bvid": m.get("bvid", ""),
                "title": m.get("title", ""),
                "cover": m.get("cover", ""),
                "duration": m.get("duration", 0),
                "owner": (m.get("upper") or {}).get("name", ""),
                "mid": (m.get("upper") or {}).get("mid", 0),
                "intro": m.get("intro", ""),
            }
            for m in medias
        ]
        return {"items": items, "has_more": d.get("has_more", False), "count": d.get("info", {}).get("media_count", 0)}

    async def get_video_info(self, bvid: str) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get(f"{API}/x/web-interface/view", params={"bvid": bvid})
            data = r.json()
        if data.get("code") != 0:
            raise BiliError(f"获取视频信息失败：{data.get('message')}")
        d = data["data"]
        return {
            "bvid": d["bvid"],
            "aid": d["aid"],
            "cid": d["cid"],
            "title": d["title"],
            "desc": d.get("desc", ""),
            "owner": d["owner"]["name"],
            "cover": d["pic"],
            "duration": d["duration"],
        }

    async def get_subtitles(self, aid: int, bvid: str, cid: int) -> list[dict[str, Any]]:
        """获取视频 AI/CC 字幕列表（需要登录凭证）。"""
        img_key, sub_key = await self._get_wbi_keys()
        params = enc_wbi({"aid": aid, "cid": cid, "bvid": bvid}, img_key, sub_key)
        async with self._client() as c:
            r = await c.get(f"{API}/x/player/wbi/v2", params=params)
            data = r.json()
        if data.get("code") != 0:
            return []
        sub = (data.get("data") or {}).get("subtitle") or {}
        out: list[dict[str, Any]] = []
        for s in sub.get("subtitles", []):
            url = s.get("subtitle_url", "")
            if url.startswith("//"):
                url = "https:" + url
            out.append({"lan": s.get("lan"), "url": url})
        return out

    async def get_audio_url(self, aid: int, bvid: str, cid: int) -> str | None:
        """通过 playurl 接口获取 dash 音频流地址（用于无字幕时的 ASR 兜底）。"""
        params: dict[str, Any] = {
            "avid": aid, "bvid": bvid, "cid": cid,
            "fnval": 16, "fnver": 0, "qn": 16,
            "platform": "pc", "high_quality": 1,
        }
        data = await self._wbi_get("https://api.bilibili.com/x/player/playurl", params)
        dash = (data.get("dash") or {}).get("audio") or []
        if not dash:
            return None
        # 取码率最低的一条（够转写用即可，省流量）
        return dash[-1].get("baseUrl") or dash[-1].get("base_url")

    async def fetch_subtitle_text(self, subtitle_url: str) -> str:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as c:
            r = await c.get(subtitle_url)
            data = r.json()
        body = (data.get("body") or [])
        return "\n".join(seg.get("content", "") for seg in body)
