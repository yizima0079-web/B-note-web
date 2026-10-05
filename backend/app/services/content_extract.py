"""三层降级内容提取管线（迁移自 video-to-summary 架构，按 B 站 + 云部署取舍）。

层 1：B 站 CC 字幕（player API，最快最准）
层 2：B 站 AI 字幕兜底（同一接口返回，标记为 AI 生成）
层 3：云端 ASR 转写
      - provider=tencent：腾讯云「录音文件识别极速版」（HTTPS 同步 POST，推荐）
      - provider=openai ：OpenAI/Groq 兼容 /audio/transcriptions
      Vercel 云函数无 ffmpeg / 无本地 GPU，因此不采用本地 faster-whisper，
      统一改用云端 ASR API，本地部署行为一致。

腾讯云「录音文件识别极速版」要点：
    - 请求：POST https://asr.cloud.tencent.com/asr/flash/v1/<appid>?{排序后参数}
    - 鉴权：签名放 Authorization 头；HMAC-SHA1(SecretKey) 对
            "POST" + host + path + "?" + 参数字典序拼接串 加密后 base64
    - Body 为音频原始二进制（≤100MB 且时长 ≤2 小时）
    - 支持 m4a/aac（B 站 dash 音频流），30 分钟音频约 10 秒返回

返回统一结构：
    {"text": str, "source": "cc" | "ai" | "asr" | "none", "detail": str}
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time

import httpx

from ..config import get_settings

log = logging.getLogger("bilirecall.extract")

# 腾讯云极速版单次上传上限（字节），文档规定 100MB
_TENCENT_FLASH_MAX_BYTES = 100 * 1024 * 1024
# 腾讯云极速版 host
_TENCENT_ASR_HOST = "asr.cloud.tencent.com"


async def _cc_or_ai_subtitle(client, info: dict) -> tuple[str, str] | None:
    """层 1/2：优先中文字幕，其次任一字幕（AI 字幕在 subs 中一并返回）。"""
    subs = await client.get_subtitles(info["aid"], info["bvid"], info["cid"])
    if not subs:
        return None
    zh = [s for s in subs if str(s.get("lan", "")).startswith("zh")]
    pick = zh[0] if zh else subs[0]
    text = await client.fetch_subtitle_text(pick["url"])
    if not text:
        return None
    source = "cc" if zh else "ai"
    label = "CC字幕" if zh else "AI字幕"
    return text, f"{label}({pick.get('lan', '?')})"


async def _get_audio_url(client, info: dict) -> str | None:
    """通过 playurl 接口拿 dash 音频流地址（需登录 cookie）。"""
    return await client.get_audio_url(info["aid"], info["bvid"], info["cid"])


# --------------------------------------------------------------------------- #
# 腾讯云「录音文件识别极速版」
# --------------------------------------------------------------------------- #
def _tencent_signature(secret_key: str, method: str, host: str, path: str, params: dict) -> str:
    """按腾讯云规则生成签名：参数字典序拼接 → HMAC-SHA1 → base64。

    签名原文 = method + host + path + "?" + "&".join(k=v, 按 key 字典序)
    """
    items = sorted((k, str(v)) for k, v in params.items() if v not in (None, ""))
    query = "&".join(f"{k}={v}" for k, v in items)
    source = f"{method}{host}{path}?{query}"
    digest = hmac.new(secret_key.encode("utf-8"), source.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


async def _tencent_flash_transcribe(audio_bytes: bytes, voice_format: str = "m4a") -> str:
    """腾讯云录音文件识别极速版：一次 HTTPS POST 同步返回结果。"""
    s = get_settings()
    if not (s.tencent_appid and s.tencent_secret_id and s.tencent_secret_key):
        raise RuntimeError("未配置腾讯云 ASR（需 TENCENT_APPID / TENCENT_SECRET_ID / TENCENT_SECRET_KEY）")
    if len(audio_bytes) > _TENCENT_FLASH_MAX_BYTES:
        raise RuntimeError(
            f"音频 {len(audio_bytes) // 1024 // 1024}MB 超过极速版 100MB 上限，"
            "请改用「录音文件识别」异步接口或降低音频码率"
        )

    host = _TENCENT_ASR_HOST
    path = f"/asr/flash/v1/{s.tencent_appid}"
    params = {
        "secretid": s.tencent_secret_id,
        "engine_type": s.tencent_engine_type,
        "voice_format": voice_format,
        "timestamp": int(time.time()),
    }
    signature = _tencent_signature(s.tencent_secret_key, "POST", host, path, params)
    query = "&".join(f"{k}={v}" for k, v in sorted((k, str(v)) for k, v in params.items()))
    url = f"https://{host}{path}?{query}"
    headers = {
        "Authorization": signature,
        "Content-Type": "application/octet-stream",
    }
    async with httpx.AsyncClient(timeout=s.asr_timeout) as c:
        r = await c.post(url, content=audio_bytes, headers=headers)
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"腾讯云 ASR 错误 {data.get('code')}: {data.get('message')}")
    results = data.get("flash_result") or []
    return "\n".join((ch.get("text") or "").strip() for ch in results if ch.get("text"))


# --------------------------------------------------------------------------- #
# OpenAI / Groq 兼容 ASR
# --------------------------------------------------------------------------- #
async def _openai_transcribe(audio_bytes: bytes, filename: str) -> str:
    s = get_settings()
    if not s.asr_api_key:
        raise RuntimeError("未配置 ASR_API_KEY，无法进行语音转写")
    files = {"file": (filename, audio_bytes, "audio/mp4")}
    data = {"model": s.asr_model}
    headers = {"Authorization": f"Bearer {s.asr_api_key}"}
    async with httpx.AsyncClient(timeout=s.asr_timeout) as c:
        r = await c.post(
            f"{s.asr_base_url.rstrip('/')}/audio/transcriptions",
            files=files, data=data, headers=headers,
        )
        r.raise_for_status()
        return r.json().get("text", "")


def _asr_ready() -> bool:
    """当前 provider 是否已配置就绪。"""
    s = get_settings()
    if s.asr_provider == "tencent":
        return bool(s.tencent_appid and s.tencent_secret_id and s.tencent_secret_key)
    return bool(s.asr_api_key)


async def _asr_long_audio(audio_url: str, max_mb: int = 96) -> str:
    """下载音频并送 ASR。

    - provider=tencent：整段下载后一次性上传（极速版同步返回，m4a 不可按字节切片）
    - provider=openai ：超过 max_mb 时按字节范围分段下载分别转写（无需 ffmpeg）
    """
    s = get_settings()
    limit = max_mb * 1024 * 1024
    async with httpx.AsyncClient(timeout=s.asr_timeout, follow_redirects=True) as c:
        head = await c.head(audio_url)
        total = int(head.headers.get("content-length", "0"))

        if s.asr_provider == "tencent":
            if 0 < total > _TENCENT_FLASH_MAX_BYTES:
                raise RuntimeError(f"音频约 {total // 1024 // 1024}MB，超过极速版单次 100MB 上限")
            audio = (await c.get(audio_url)).content
            return await _tencent_flash_transcribe(audio, voice_format=s.tencent_voice_format)

        # OpenAI 兼容路径
        if 0 < total <= limit:
            audio = (await c.get(audio_url)).content
            return await _openai_transcribe(audio, "audio.m4a")
        if total <= 0:
            raise RuntimeError("无法获取音频大小，跳过 ASR")
        parts: list[str] = []
        step = limit
        for start in range(0, total, step):
            end = min(start + step - 1, total - 1)
            chunk = (await c.get(audio_url, headers={"Range": f"bytes={start}-{end}"})).content
            parts.append(await _openai_transcribe(chunk, f"part{start}.m4a"))
            await asyncio.sleep(0.3)
        return "\n".join(p for p in parts if p)


async def extract_content(client, info: dict, progress_cb=None) -> dict:
    """完整降级链入口。progress_cb(stage: str) 用于前端进度展示。"""
    def tick(stage: str):
        if progress_cb:
            try:
                progress_cb(stage)
            except Exception:  # noqa: BLE001
                pass

    # 层 1/2：字幕
    tick("提取字幕中")
    try:
        sub = await _cc_or_ai_subtitle(client, info)
        if sub:
            return {"text": sub[0], "source": sub[1], "detail": sub[1]}
    except Exception as exc:  # noqa: BLE001
        log.warning("字幕获取失败 %s: %s", info.get("bvid"), exc)

    # 层 3：ASR
    s = get_settings()
    if not _asr_ready():
        return {"text": "", "source": "none", "detail": "无字幕且未配置语音转写"}
    tick("无字幕，语音转写中（较慢）")
    try:
        audio_url = await _get_audio_url(client, info)
        if not audio_url:
            return {"text": "", "source": "none", "detail": "无法获取音频流"}
        text = await _asr_long_audio(audio_url)
        if text.strip():
            label = "腾讯云ASR" if s.asr_provider == "tencent" else f"ASR({s.asr_model})"
            return {"text": text, "source": "asr", "detail": label}
    except Exception as exc:  # noqa: BLE001
        log.warning("ASR 失败 %s: %s", info.get("bvid"), exc)
        return {"text": "", "source": "none", "detail": f"语音转写失败: {str(exc)[:80]}"}
    return {"text": "", "source": "none", "detail": "转写结果为空"}
