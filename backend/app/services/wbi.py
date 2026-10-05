"""B 站 WBI 签名（2023 起多数 web 接口需要）。

流程：从 nav 接口取 img_key / sub_key → 按固定乱序表重排得到 mixin_key
→ 参数排序后拼 mixin_key 做 MD5，得到 w_rid，并附加 wts 时间戳。
"""
from __future__ import annotations

import hashlib
import time
from functools import reduce
from urllib.parse import urlencode

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def _filter_value(value: str) -> str:
    return "".join(c for c in str(value) if c not in "!'()*")


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """返回加入 wts 与 w_rid 的参数字典。"""
    mixin_key = get_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = int(time.time())
    params = {k: _filter_value(v) for k, v in sorted(params.items())}
    query = urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params
