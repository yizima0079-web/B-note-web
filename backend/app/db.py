"""SQLite 数据访问层（SQLAlchemy 2.0，同步引擎，MVP 足够）。"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
os.makedirs(_settings.data_dir, exist_ok=True)
_db_path = os.path.join(_settings.data_dir, "bilirecall.db")

engine = create_engine(
    f"sqlite:///{_db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401  确保模型注册后再建表

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
