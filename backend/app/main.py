"""FastAPI 应用入口：装配中间件、路由，并托管前端静态资源。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .routers import auth, bilibili, ima, notes
from .services.task_queue import TaskQueue

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    notes.set_queue(TaskQueue(max_concurrency=get_settings().max_concurrent_tasks))
    yield


app = FastAPI(title="哔记 BiliRecall", version="0.1.0", lifespan=lifespan)

settings = get_settings()
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(bilibili.router)
app.include_router(notes.router)
app.include_router(ima.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "bilirecall", "version": "0.1.0"}


# 前端静态资源（放在最后，避免遮挡 /api 路由）
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
