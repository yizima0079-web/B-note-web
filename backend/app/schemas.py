"""请求 / 响应模型（Pydantic v2）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str


class VideoRef(BaseModel):
    bvid: str
    title: str = ""
    owner: str = ""
    cover: str = ""
    duration: int = 0


class UrlRef(BaseModel):
    """通过粘贴 URL 添加的视频引用（创建任务时会被解析为 VideoRef）。"""
    url: str


class CollectionScope(BaseModel):
    """合集（收藏夹内某系列）选段：从某期开始，取 N 期。"""

    series_key: str = Field(description="合集标识：season_id 或 up 主 mid")
    start_index: int = Field(default=1, ge=1)
    count: int = Field(default=10, ge=1, le=200)


class JobCreateRequest(BaseModel):
    title: str = ""
    videos: list[VideoRef] = Field(default_factory=list)
    urls: list[UrlRef] = Field(default_factory=list)  # 新增：支持粘贴 URL
    collections: list[CollectionScope] = Field(default_factory=list)
    # 生成选项
    style: str = "knowledge"  # knowledge / outline / exam / podcast
    language: str = "zh"


class JobItemOut(BaseModel):
    id: int
    bvid: str
    title: str
    owner: str
    cover: str
    duration: int
    status: str
    error: str

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: int
    title: str
    status: str
    total: int
    done: int
    failed: int
    message: str
    created_at: str

    model_config = {"from_attributes": True}


class JobDetailOut(JobOut):
    items: list[JobItemOut] = Field(default_factory=list)
