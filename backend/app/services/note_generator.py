"""知识笔记生成：视频信息 + 提取文本 → OpenAI 兼容 LLM → 结构化 Markdown。

长文本策略（迁移自 video-to-summary 的 chunking 思路）：
- 文本 ≤ chunk_size：单次直接生成（reduce 免了，省 token）
- 文本 > chunk_size：分块并发 map（每块提炼要点）→ 汇总 reduce（生成最终笔记）
  相比旧版「截断到 12000 字」，长视频内容不再丢失。
"""
from __future__ import annotations

import asyncio

import httpx

from ..config import get_settings

STYLE_PROMPTS = {
    "knowledge": "围绕知识结构化整理：先给一句话总结，再给核心要点、关键概念、可行动清单与复盘问题。",
    "outline": "以层级大纲形式梳理视频的逻辑脉络，突出论点—论据结构。",
    "exam": "面向考试/考核：提炼必须掌握的知识点、易错点与记忆口诀。",
    "podcast": "以口语化笔记形式记录，保留讲述者的观点与关键例子，便于日后复述。",
}


def build_system_prompt(style: str) -> str:
    style_hint = STYLE_PROMPTS.get(style, STYLE_PROMPTS["knowledge"])
    return (
        "你是一位严谨的知识整理助手，专门把 B 站视频内容提炼成可快速复盘的中文知识笔记。"
        f"风格要求：{style_hint}\n"
        "输出要求：\n"
        "1. 使用 Markdown；\n"
        "2. 禁止杜撰视频中不存在的信息，若内容缺失或过短，需在开头标注『内容不足』并给出基于标题与简介的谨慎概括；\n"
        "3. 结构：先 `# 标题`，再 `> 一句话总结`，然后依次是 `## 核心要点`、`## 关键概念`、`## 可行动清单`、`## 复盘问题`；\n"
        "4. 语言精炼，要点使用短句或列表。"
    )


def build_user_prompt(meta: dict, body: str) -> str:
    return (
        f"视频标题：{meta.get('title', '')}\n"
        f"UP 主：{meta.get('owner', '')}\n"
        f"时长（秒）：{meta.get('duration', 0)}\n"
        f"简介：{(meta.get('desc') or '')[:1000]}\n\n"
        f"内容文本：\n{body}"
    )


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """按字符分块，带少量重叠避免切断语义。"""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    blocks: list[str] = []
    step = size - overlap
    for start in range(0, len(text), step):
        blocks.append(text[start:start + size])
        if start + size >= len(text):
            break
    return blocks


async def _chat(s, messages: list[dict]) -> str:
    payload = {"model": s.llm_model, "messages": messages, "temperature": 0.3}
    headers = {"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=s.llm_timeout) as c:
        r = await c.post(f"{s.llm_base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            if r.status_code == 401:
                raise RuntimeError(
                    "LLM 鉴权失败(401)：LLM_API_KEY 无效或未生效。"
                    f"已加载 key 形如 {s.llm_api_key[:6]}...{s.llm_api_key[-4:] if len(s.llm_api_key) > 10 else ''}，"
                    f"服务端返回：{detail}"
                )
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def generate_note(meta: dict, text: str, style: str = "knowledge") -> str:
    """生成最终笔记。长文本自动走 map-reduce 分块管线。"""
    s = get_settings()
    if not s.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY，无法生成笔记")
    text = text.strip()
    if len(text) <= s.chunk_size:
        body = text if text else "（未获取到字幕或转写内容，请基于标题与简介谨慎概括）"
        return await _chat(s, [
            {"role": "system", "content": build_system_prompt(style)},
            {"role": "user", "content": build_user_prompt(meta, body)},
        ])

    # map：分块并发提炼
    blocks = chunk_text(text, s.chunk_size, s.chunk_overlap)
    sem = asyncio.Semaphore(max(1, s.map_concurrency))

    async def map_one(i: int, blk: str) -> str:
        async with sem:
            return await _chat(s, [
                {"role": "system", "content": (
                    "你是笔记提炼助手。以下是一段长视频的第 %d/%d 段内容，"
                    "请提炼该段的要点与关键概念（Markdown 列表，保留具体事实与数据，不添加评价）。"
                    % (i, len(blocks))
                )},
                {"role": "user", "content": blk},
            ])

    part_notes = await asyncio.gather(*[map_one(i + 1, b) for i, b in enumerate(blocks)])

    # reduce：汇总成最终笔记
    digest = "\n\n".join(f"### 第 {i+1} 段\n{n}" for i, n in enumerate(part_notes))
    return await _chat(s, [
        {"role": "system", "content": build_system_prompt(style)},
        {"role": "user", "content": build_user_prompt(meta, "以下是分段提炼稿，请汇总去重后输出最终笔记：\n\n" + digest)},
    ])
