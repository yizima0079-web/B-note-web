"""B 站链接解析工具：从各种 B 站 URL 中提取 bvid / avid / 合集标识。"""
import re
from urllib.parse import parse_qs, urlparse

BV_PATTERN = re.compile(r"(BV[a-zA-Z0-9]+)")

def extract_bvid(url: str) -> str | None:
    """从 B 站 URL 中提取 bvid（优先），失败则返回 None。"""
    # 直接 BV 号
    if url.startswith("BV"):
        return url[:12]
    m = BV_PATTERN.search(url)
    if m:
        return m.group(1)
    # 短链/常规链接里找 bvid 参数
    try:
        p = urlparse(url)
        q = parse_qs(p.query)
        if "bvid" in q:
            return q["bvid"][0]
    except Exception:
        pass
    return None


def extract_avid(url: str) -> int | None:
    """尝试提取 avid（备用）。"""
    try:
        p = urlparse(url)
        q = parse_qs(p.query)
        if "aid" in q:
            return int(q["aid"][0])
        # path 里 /video/12345678 形式
        parts = [x for x in p.path.split("/") if x]
        for i, seg in enumerate(parts):
            if seg.isdigit() and i > 0 and parts[i-1] in ("video", "list"):
                return int(seg)
    except Exception:
        pass
    return None


def is_bilibili_url(url: str) -> bool:
    return "bilibili.com" in url or url.startswith("BV") or (url.startswith("http") and extract_bvid(url))
