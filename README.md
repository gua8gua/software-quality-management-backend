# 软件质量管理后端启动说明

## 安装依赖

使用 Python 3.11 或 3.12，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 配置数据库

### PostgreSQL

安装并启动 Docker Desktop，在项目根目录执行：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d postgres
```

编辑 `.env`，使 `DATABASE_URL` 与 `POSTGRES_*` 配置一致：

```dotenv
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=software_quality_management
POSTGRES_USER=postgres
POSTGRES_PASSWORD=你的数据库密码
DATABASE_URL=postgresql+asyncpg://postgres:你的数据库密码@localhost:5432/software_quality_management
```

执行数据库迁移：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

### SQLite

使用 SQLite 运行项目资料库、TLR、需求分析和一致性分析时，在尚未创建 `.env` 的项目根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/init_local_tlr.py
.\.venv\Scripts\python.exe scripts/init_consistency.py
```

已有 SQLite 数据库执行：

```powershell
.\.venv\Scripts\python.exe scripts/upgrade_local_tlr.py
.\.venv\Scripts\python.exe scripts/init_consistency.py
```

文档向量检索服务使用 PostgreSQL + pgvector。

## 配置模型

在 `.env` 中设置：

```dotenv
MODEL_BASE_URL=https://你的向量模型服务/v1
MODEL_API_KEY=你的向量模型密钥
EMBEDDING_MODEL=你的向量模型名称
EMBEDDING_DIMENSION=1536
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://你的对话模型服务/v1
LLM_API_KEY=你的对话模型密钥
LLM_MODEL=你的对话模型名称
LLM_TIMEOUT_SECONDS=60
```

`EMBEDDING_DIMENSION` 填写实际模型的向量维度。启动前端后，也可在 `#/models` 配置模型连接及任务绑定。

需要使用 Java LiSSA 检索时，安装 JDK 17 和 Maven，并执行：

```powershell
$env:Path = "$(Join-Path $PWD '.venv\Scripts');$env:Path"
.\scripts\build_lissa.ps1
```

## 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

接口文档：`http://localhost:8000/docs`。

启动完整界面：按照 [Agent 启动说明](../software-quality-agent/README.md) 启动 Agent 和前端，打开 `http://localhost:5173` 或构建后的 `http://localhost:8010`。

## Docker 启动

配置 `.env` 中的数据库和模型参数后执行：

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail 50 api
```

容器启动时执行数据库迁移并监听 `API_PORT`，默认 8000。

## 停止

本机服务在终端按 `Ctrl+C`。Docker 服务执行：

```powershell
docker compose stop
```
