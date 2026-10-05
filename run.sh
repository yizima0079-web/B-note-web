#!/usr/bin/env bash
# 本地一键启动（开发模式）
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "未找到 .env，正在从 .env.example 复制…"
  cp .env.example .env
  echo "请编辑 .env 设置 SECRET_KEY / APP_PASSWORD / LLM_API_KEY 后重新运行。"
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r backend/requirements.txt

cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
