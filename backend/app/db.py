"""数据访问层：优先托管 Postgres（DATABASE_URL），无则回退本地 SQLite。

硬约束（Vercel / Serverless 环境）：
- 模块导入阶段零副作用：不建目录、不解析连接串、不连库。只读文件系统下「导入即崩」的根因就在这里。
- 建表只在 ``init_db()`` 里发生，由 FastAPI lifespan 在应用启动时调用。
- 连接池用 ``NullPool``：函数实例随时冻结/回收，复用连接只会积累死连接。
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings

logger = logging.getLogger("bilirecall.db")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def normalize_database_url(raw: str) -> str:
    """把各家控制台给的连接串统一成 SQLAlchemy + psycopg3 可用的形式。"""
    url = raw.strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def is_serverless() -> bool:
    return bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def _sqlite_path() -> str:
    """SQLite 只用于本地开发与应急演示。

    Vercel 上唯一可写目录是 /tmp，且不持久、实例间不共享，
    因此即便落到 /tmp 也只能跑演示，不能存业务数据（订单/积分/排名等）。
    """
    data_dir = (get_settings().data_dir or "./data").strip()
    if is_serverless() and not data_dir.startswith("/tmp"):
        data_dir = "/tmp/bilirecall"
    os.makedirs(data_dir, exist_ok=True)  # 仅当真的使用 SQLite 时才碰文件系统
    return os.path.join(data_dir, "bilirecall.db")


def _build_engine() -> Engine:
    database_url = (get_settings().database_url or "").strip()
    if not database_url:
        path = _sqlite_path()
        logger.warning(
            "未配置 DATABASE_URL，回退 SQLite：%s（%s）",
            path,
            "Serverless 临时目录，冷启动即丢数据，仅可演示"
            if is_serverless()
            else "仅适合本地开发",
        )
        return create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            future=True,
        )

    url = normalize_database_url(database_url)
    connect_args: dict[str, object] = {}
    # Supabase 连接池 / Neon pooled 端口走事务级 PgBouncer，必须关闭预处理语句
    if "pooler.supabase" in url or ":6543/" in url:
        connect_args["prepare_threshold"] = None
    logger.info("使用托管数据库：%s", url.split("@")[-1])
    return create_engine(url, connect_args=connect_args, poolclass=NullPool, future=True)


def get_engine() -> Engine:
    """首次调用时才创建引擎，模块导入不触发。"""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factory


def SessionLocal() -> Session:
    """保持 `SessionLocal()` 的调用签名，内部惰性初始化。"""
    return _get_session_factory()()


def init_db() -> None:
    """建表。只在应用启动（lifespan）调用，不在导入期执行。"""
    from . import models  # noqa: F401  确保模型注册后再建表

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("数据表已就绪（backend=%s）", engine.dialect.name)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
