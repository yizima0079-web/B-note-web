# 哔记 BiliRecall · MVP

把 B 站收藏夹里"囤了却没时间看"的视频，一键生成结构化**知识笔记**，用于快速总结与复盘。

> 痛点闭环：**扫码登录 → 浏览收藏夹 → 勾选视频 / 合集片段（可指定从第几期开始、取多少期）→ 异步生成笔记 → 查看 / 导出 Markdown**。

---

## 1. 系统框架

```
┌────────────┐   HTTPS    ┌───────────────────────────────┐
│  浏览器     │ ─────────▶ │  FastAPI 应用（单服务）         │
│  单文件 UI  │            │  ├─ /api/auth     口令+JWT会话  │
└────────────┘            │  ├─ /api/bilibili 扫码/收藏夹   │
                          │  ├─ /api/notes    任务/笔记/导出 │
                          │  └─ 托管前端静态资源            │
                          └──────┬─────────────┬──────────┘
                                 │             │
                        ┌────────▼───┐  ┌──────▼───────────┐
                        │  SQLite    │  │  asyncio 任务队列  │
                        │  账号/任务/ │  │  抓字幕→调LLM→落库 │
                        │  笔记       │  └──────┬───────────┘
                        └────────────┘         │
                                    ┌───────────▼────────────┐
                                    │ B站开放接口(WBI签名)     │
                                    │ + OpenAI兼容 LLM 接口   │
                                    └────────────────────────┘
```

### 模块职责
| 模块 | 文件 | 说明 |
|---|---|---|
| 配置 | `backend/app/config.py` | 环境变量集中管理 |
| 安全 | `backend/app/security.py` | JWT 会话、Fernet 凭证加密、口令校验 |
| 鉴权依赖 | `backend/app/deps.py` | 会话校验 + CSRF 双提交校验 |
| 数据模型 | `backend/app/models.py` | 账号、任务、笔记条目 |
| B站客户端 | `backend/app/services/bili_client.py` | 扫码登录、收藏夹、视频详情、字幕 |
| WBI 签名 | `backend/app/services/wbi.py` | 新版接口签名 |
| 笔记生成 | `backend/app/services/note_generator.py` | 字幕+元信息 → 结构化 Markdown |
| 任务队列 | `backend/app/services/task_queue.py` | 并发生成、进度落库 |
| 路由 | `backend/app/routers/*.py` | auth / bilibili / notes |
| 前端 | `frontend/index.html` | 单文件 Dashboard UI |

---

## 2. 登录验证与安全防护

| 层面 | 措施 |
|---|---|
| 应用访问 | 单用户**口令登录**（`APP_PASSWORD`），签发 **JWT** 存于 **HttpOnly + SameSite=Lax** Cookie |
| 状态变更接口 | **CSRF 双提交校验**：Cookie 与请求头中的令牌必须一致 (POST/DELETE) |
| B站凭证 | `SESSDATA` 等 Cookie **不明文落库**，使用由 `SECRET_KEY` 经 PBKDF2 派生的 **Fernet 密钥加密** |
| 传输 | 生产启用 HTTPS 时设 `COOKIE_SECURE=true`，Cookie 仅走加密通道 |
| 越权 | 所有业务接口均要求有效会话；未绑定 B 站账号时拒绝收藏夹访问 |

> 后续可扩展：接入 OAuth（B站/Bitwarden 式）、多用户、审计日志。

---

## 3. 快速开始

### 方式 A：Docker（推荐部署到云主机）
```bash
git clone <this-repo> && cd bilirecall-mvp
cp .env.example .env         # 编辑 SECRET_KEY / APP_PASSWORD / LLM_API_KEY
docker compose up -d --build
# 访问 http://<服务器IP>:8000
```

### 方式 B：本地开发（Linux / macOS）
```bash
cp .env.example .env         # 填写配置
bash run.sh                  # 自动建虚拟环境并启动
# 访问 http://localhost:8000
```

### 方式 C：本地开发（Windows，无需 Docker）
1. 安装 Python **3.11 或 3.12（推荐）**，或 3.13 / 3.14（均可，见下方说明）。
   - 安装时务必勾选 **"Add Python to PATH"**：https://www.python.org/downloads/
2. 复制配置：把 `.env.example` 复制为 `.env`，编辑 `SECRET_KEY` / `APP_PASSWORD` / `LLM_API_KEY`。
3. 双击运行 `run.bat`（脚本会自动升级 pip、建虚拟环境、装依赖、启动服务）。
4. 浏览器打开 http://localhost:8000

#### 依赖安装排错：`Building wheel for pydantic-core ... failed` / `exit code: 1`
如果安装依赖时报 `error: the configured Python interpreter version (3.14) is newer than PyO3's maximum supported version`，
说明 pip 拉到了**旧版 `pydantic-core`** 并尝试用 Rust 源码编译。原因与解决：

- **原因**：本机 Python 版本较新（如 3.14），而旧的 `pydantic`（如 2.10.x）所依赖的 `pydantic-core` 尚无该版本预编译包，pip 只能现场编译 Rust 代码，最终失败。
- **解决（推荐）**：本项目 `backend/requirements.txt` 已改为**下限约束**写法，会自动选用带 Python 3.14 预编译 wheel 的新版 `pydantic`（2.12+）。
  请先升级 pip 再安装：
  ```bat
  python -m pip install --upgrade pip
  pip install -r backend\requirements.txt
  ```
- **若仍失败**：改用 **Python 3.12**（兼容性最佳），并用锁定版清单安装：
  ```bat
  pip install -r backend\requirements.lock.txt
  ```
- 不要使用 pip 官方源之外的镜像可能导致版本滞后；如使用镜像，请确认其已同步最新 `pydantic`。

> 若 `docker compose` 提示「不是内部或外部命令」，说明本机未安装 Docker。
> 可任选其一：① 用上面的方式 C 直接跑（最省事）；② 安装 Docker Desktop 后再用方式 A。

手动等价命令（Windows PowerShell / CMD）：
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 关键环境变量
| 变量 | 说明 |
|---|---|
| `SECRET_KEY` | 会话签名 + 凭证加密根密钥，**务必改为随机长串** |
| `APP_PASSWORD` | 网页登录口令 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 任意 OpenAI 兼容接口（DeepSeek / 通义 / Kimi / OpenAI / Ollama） |
| `DATA_DIR` | SQLite 及数据目录，默认 `./data` |

### 云平台部署
- **Railway / Render / Fly.io**：直接以 `Dockerfile` 构建，挂载持久卷到 `/app/backend/data`。
- **VPS**：`docker compose up -d`，用 Nginx/Caddy 反代并启用 HTTPS（记得设 `COOKIE_SECURE=true`）。
- 前端与后端同源部署，无需额外跨域配置。

---

## 4. API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 口令登录 |
| GET | `/api/auth/session` | 会话状态 |
| GET | `/api/bilibili/qr` | 获取登录二维码（base64 PNG） |
| GET | `/api/bilibili/qr/poll` | 轮询扫码结果并落库凭证 |
| GET | `/api/bilibili/folders` | 收藏夹列表 |
| GET | `/api/bilibili/folders/{id}/resources` | 收藏夹内容（分页） |
| POST | `/api/notes/jobs` | 创建笔记生成任务 |
| GET | `/api/notes/jobs/{id}` | 任务进度与条目 |
| GET | `/api/notes/items/{id}` | 单条笔记 |
| GET | `/api/notes/items/{id}/markdown` | 导出 Markdown |

---

## 5. 已预留的扩展位（后期再加）

- `backend/app/services/note_generator.py` 的 `STYLE_PROMPTS` / `build_system_prompt`：接入你提供的**标准报告格式 skill**，即可强约束输出。
- **MCP 封装**：现有 `/api/*` 可直接被 MCP Server 包装为工具（如 `list_folders` / `create_note_job` / `get_note`）。
- **Obsidian / 个人知识库**：`/api/notes/items/{id}/markdown` 已支持导出，可对接 Local REST API 或同步目录。

---

## 6. 目录结构
```
bilirecall-mvp/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py  config.py  security.py  deps.py  db.py  models.py  schemas.py
│       ├── routers/   auth.py  bilibili.py  notes.py
│       └── services/  bili_client.py  wbi.py  note_generator.py  task_queue.py
├── frontend/index.html
├── Dockerfile  docker-compose.yml  run.sh  run.bat  .env.example
└── README.md
```

## 7. 免责声明
本项目仅用于个人学习与自身账号内容的整理，请遵守 B 站用户协议与相关法律法规，勿用于批量抓取、商业分发或任何侵害平台与他人权益的用途。
