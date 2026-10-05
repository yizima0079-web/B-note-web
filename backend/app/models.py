"""数据模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class BiliAccount(Base):
    """B 站账号及其加密凭证（自托管单用户场景通常只有一行）。"""

    __tablename__ = "bili_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    mid: Mapped[str] = mapped_column(String(32), index=True)
    uname: Mapped[str] = mapped_column(String(128), default="")
    face: Mapped[str] = mapped_column(String(256), default="")
    # 加密后的 cookie 串（SESSDATA / bili_jct / DedeUserID 等，JSON）
    enc_cookies: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NoteJob(Base):
    """一次「收藏夹 → 知识笔记」生成任务。"""

    __tablename__ = "note_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    scope_json: Mapped[str] = mapped_column(Text, default="{}")  # 选中范围（视频/合集/期数）
    status: Mapped[str] = mapped_column(String(24), default="pending")  # pending/running/done/failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    items: Mapped[list["NoteItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="NoteItem.id"
    )


class NoteItem(Base):
    """单个视频的知识笔记。"""

    __tablename__ = "note_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("note_job.id", ondelete="CASCADE"), index=True)
    bvid: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    owner: Mapped[str] = mapped_column(String(128), default="")
    cover: Mapped[str] = mapped_column(String(256), default="")
    duration: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")  # pending/running/done/failed
    error: Mapped[str] = mapped_column(String(512), default="")
    note_md: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    job: Mapped[NoteJob] = relationship(back_populates="items")


class ImaAccount(Base):
    """ima 知识库连接配置（自托管单用户场景通常只有一行）。"""

    __tablename__ = "ima_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128), default="")
    # 加密后的 API Key（与 B 站凭证同一套 Fernet 加密）
    enc_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
