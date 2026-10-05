# 部署与环境变量规范

## 铁律

1. 仓库只提交 `.env.example`（变量名 + 占位值），真实值永不入库。
2. `.env` / `.env.local` / `backend/.env` 全部在 `.gitignore` 中；`git ls-files | Select-String env` 必须只剩 `.env.example`。
3. 真实值统一配在部署平台的 Environment Variables 控制台，本地用 `vercel env pull` 拉取，手抄必错。
4. 改完环境变量必须 **Redeploy**，否则线上仍跑旧值。
5. 密钥一旦误提交：先去服务商后台 **轮换**（LLM / 腾讯云 / `SECRET_KEY`），再清历史，删文件没用。

## 本项目的环境变量（全部为服务端密钥，无前端公开变量）

| 变量 | 用途 | 泄露后果 |
|---|---|---|
| `SECRET_KEY` | 签发会话 JWT + 派生 Fernet 密钥加密 B 站凭证 | 伪造会话、解密凭证 |
| `APP_PASSWORD` | 网页访问口令 | 直接登录 |
| `LLM_API_KEY` | DeepSeek / OpenAI 兼容接口 | 盗刷额度 |
| `TENCENT_APPID` / `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` | 腾讯云 ASR | 盗刷额度 |
| `ASR_API_KEY` | Groq / OpenAI 兼容 ASR | 盗刷额度 |
| `COOKIE_SECURE` / `CORS_ORIGINS` / `DATA_DIR` 等 | 运行参数 | — |

> 本项目前端是后端托管的单文件 `frontend/index.html`，不读构建期变量，
> 因此 **不存在** `NEXT_PUBLIC_*`。将来若接入 Next.js：可公开的才加 `NEXT_PUBLIC_` 前缀，
> 服务端密钥（Supabase service role key、本表所有项）严禁加该前缀，否则会被打进浏览器 bundle。

## Vercel 配置步骤

```bash
npm i -g vercel
vercel login
vercel link                     # 关联到 B-note-web 项目
vercel env add SECRET_KEY production
vercel env add SECRET_KEY preview
# ... 其余变量同理，按 Production / Preview 分开配置
vercel env pull backend/.env.local   # 本地同步，勿手抄
vercel --prod                   # 改完变量必须重新部署
```

Pull 到 `backend/.env.local` 的原因：后端进程的 CWD 是 `backend/`，`config.py` 的加载顺序是
`.env` → `.env.local`，后者覆盖前者，因此从控制台拉下来的值会直接生效，且该文件已被 `.gitignore` 排除。

Preview 环境建议接独立的测试库 / 测试 Key，避免预览站污染生产数据。

## 现实约束（重要）

当前代码是 **FastAPI + SQLite + asyncio 后台任务队列** 的单体服务：

- Vercel Serverless 函数无持久磁盘，`backend/data/*.db` 写进去下次调用就没了；
- 生成笔记是长任务（抓字幕 → 调 LLM → 落库，单次可达数分钟），会超出函数执行上限；
- `asyncio` 队列随实例回收而丢失，任务进度无法保证。

因此把整个仓库直接部署到 Vercel **跑不通业务流程**。三条路，按推荐度排序：

1. **后端上常驻容器平台**（Railway / Render / Fly.io / 自己的 VPS），Vercel 只做前端 + 反代 `/api`；
   数据库换 Postgres（如 Supabase），任务队列换平台任务或独立 worker。
2. **整体上 VPS + Docker**：仓库自带 `Dockerfile` 与 `docker-compose.yml`，`docker compose up -d --build` 即可，改动最小。
3. **真要全量上 Vercel**：改造为 Next.js 前端 + Serverless API + Postgres + 外部任务队列，
   算重写而非部署，成本最高。

Vercel 侧唯一能立刻用上的配置是环境变量本身：三个环境（Development / Preview / Production）的密钥隔离，
以及 `vercel env pull` 的同步流程——这套规范与最终选哪个平台无关，先落地不会有坏处。
