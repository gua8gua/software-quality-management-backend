# 软件质量管理后端

## 开发与协作规范（Git Workflow）

采用特性分支工作流与 Pull Request 审查机制，参照工作区 `日志/README.md`。

- `main` 用于稳定发布，`develop` 用于开发集成；禁止直接在这两个分支开发或推送，通过 PR 合并。
- 每次开发前先保存当前改动，切换到 `develop` 并同步，再创建 `feature/姓名缩写-功能` 或 `bugfix/姓名缩写-问题` 分支。
- 按模块小步提交；提交信息使用 `feat`、`fix`、`docs`、`refactor`、`test`、`style` 或 `chore` 前缀。
- 开发期间及时同步 `develop`，尽早解决冲突；自己的特性分支可以定期推送备份。禁止强推公共分支。
- PR 必须选择 `base: develop`，说明问题、修改行为、基线来源、迁移影响和验证结果；经同事审查后合并。
- 不提交密钥、`.env`、虚拟环境、IDE 配置、数据库、模型缓存、日志或构建产物；原始资料保留来源与版本，下载依赖通过固定版本脚本复现。
- 新功能尽量在独立模块内完成；共享 API、模型提供者接口和数据库迁移的变更需在 PR 中明确。每个迁移有唯一版本号，不修改已应用迁移。

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/yourname-topic
# 开发并运行相关测试，按实际修改文件 git add 后提交
git commit -m "feat: describe the change"
git fetch origin
git merge origin/develop
git push -u origin feature/yourname-topic
# 创建 PR：feature/yourname-topic -> develop；审查合并后再清理
git switch develop
git pull --ff-only origin develop
git branch -d feature/yourname-topic
```

这个项目从 `rag-center` 复制而来，保留同样的技术栈和分层方式：
`FastAPI + SQLAlchemy 2.x async + Alembic + PostgreSQL/pgvector + Pydantic Settings`。

当前业务目标改成**通用软件质量管理系统**，先聚焦两类资产：

1. 生命周期文档：需求、设计、测试、评审、发布说明等
2. 代码：仓库代码、模块文件、测试代码

后续要做的核心事情是：

- 文档和需求的一致性检查
- 文档之间的一致性检查
- 文档与代码的一致性检查
- 代码功能与需求的一致性检查
- 测试对需求的覆盖分析

## 当前定位

这是一个“资料接入 + 质量分析底座”，不是纯 RAG 产品。
底层仍复用原来的文档切片、向量存储、重排和统一异常处理能力，方便后面快速扩展到：

- 需求追踪
- 变更影响分析
- 测试覆盖评估
- 质量门禁
- 证据链汇总

## 技术栈

- Python 3.11
- FastAPI
- Pydantic v2 / pydantic-settings
- SQLAlchemy 2.x async
- Alembic
- PostgreSQL 16 + pgvector 16
- Docker / Docker Compose
- OpenAI-compatible embedding / LLM 接口

## 目录结构

```text
app/
  api/              # HTTP 接口与依赖注入
  core/             # 配置、日志、异常
  db/               # SQLAlchemy Base 与异步会话
  models/           # 持久化模型
  schemas/          # 请求 / 响应模型
  repositories/     # 数据访问层
  providers/        # 文档、embedding、LLM、重排、向量库适配器
  services/         # 业务编排
  utils/            # id、切分器等通用工具
migrations/         # Alembic 迁移
tests/              # 单元与契约测试
```

## 当前可复用能力

- 文档接入与切片
- 向量化索引
- pgvector 相似度检索
- LLM 重排开关
- 统一错误码与日志
- 异步 SQLAlchemy 数据层

## 首次部署（本地 SQLite，仅第一次执行）

适用于项目资料库和 TLR，不依赖 Docker。使用 Python 3.11 或 3.12。以下命令从工作区根目录执行；新机器需按实际位置调整路径。

```powershell
cd E:\PyFile\Differentiated_Quality_Management_NG_EIE\projects\0_\software-quality-management-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m scripts.init_local_tlr
```

初始化脚本创建 `.env` 和 `data/tlr.sqlite` 中的 TLR 表；已有 `.env` 会退出，不覆盖配置。此模式不要预先复制 `.env.example`，也不要执行包含 pgvector 的完整 Alembic 迁移。初始化不自动导入样例；启动后可在前端新建项目并上传资料。

当前工作区已经完成这一步，并导入 SMOS 的 67 份需求和 100 份代码，可直接使用下面的日常启动指令。真实检测可在前端“模型配置”中填写 OpenAI 兼容接口并为任务选模型，无需重启；未设置任务绑定时才回退到 `.env` 中的 MODEL_* 和 LLM_* 配置。

## 日常启动（当前工作区使用这一节）

打开两个 PowerShell 窗口。窗口 1 启动后端：

```powershell
cd E:\PyFile\Differentiated_Quality_Management_NG_EIE\projects\0_\software-quality-management-backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

窗口 2 启动前端，注意进入 `frontend` 子目录：

```powershell
cd E:\PyFile\Differentiated_Quality_Management_NG_EIE\projects\0_\software-quality-agent\frontend
$env:QUALITY_BACKEND_URL = "http://127.0.0.1:8000"
npm run dev
```

浏览器打开 **http://localhost:5173**，工作空间为 `local`；若 Vite 提示端口被占用，以终端实际地址为准。后端接口文档是 http://127.0.0.1:8000/docs，项目接口是 http://127.0.0.1:8000/api/v1/tlr/projects?tenant_id=local 。8000 首页是服务说明，不是项目页面。

保持两个窗口运行，按 Ctrl+C 停止；下次重复本节即可。无需重复安装、初始化、导入或切换 Git 分支。SQLite 数据在重启后保留，不需要启动数据库进程。不要同时启动第二个占用同端口的服务。

## 首次部署（PostgreSQL + Docker，可选完整模式）

需要 Docker Compose，支持旧 pgvector RAG 和 TLR。与本地 SQLite 模式二选一；切换数据库不会自动迁移已有 SQLite 数据。先保存已有 `.env` 和数据库，再按 `.env.example` 手工准备 PostgreSQL 配置，不覆盖原配置。

在后端目录执行：

```powershell
# 仅在没有 .env 时复制；已有文件需按部署模式手工检查
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# 编辑并确认 .env 的 POSTGRES_*、API_PORT 和模型参数后
# 首次构建镜像并启动，容器入口会执行 alembic upgrade head
docker compose up -d --build
```

前端首次依赖安装见相邻 [前端 README](../software-quality-agent/frontend/README.md)。本地 `.venv` 和 Docker API 不要同时占用 8000。

## 日常启动（仅 Docker 部署用户）

```powershell
# 后端目录；先确保 Docker 已运行
docker compose up -d
# 检查状态和后端日志
docker compose ps
docker compose logs --tail 50 api
```

前端仍按日常启动节执行 `npm run dev`。停止容器使用 `docker compose stop`，数据保留在 postgres_data 卷中。后端代码或依赖变化时重新执行 `docker compose up -d --build`。

## 更新与测试

依赖变化时才重新安装；PostgreSQL 的新迁移由 Docker 入口应用，本机运行 PostgreSQL 后端则手工执行 `.\.venv\Scripts\python.exe -m alembic upgrade head`。SQLite 当前初始化脚本不承担已有表结构升级，更新结构需专门迁移，不要删除数据库重新初始化。

`tests.console_server:app`、18080 和 15173 只用于独立测试，不是日常启动入口，详见前端 README。当前 SQLite 模式仅支持 TLR/资料库；旧向量 RAG 需 PostgreSQL。

## TLR 分层与结构升级

已增加多类型制品分割、层间比较矩阵和原始结构关系。详见 [分层、数据集与复现说明](docs/tlr-layers-and-structure.md)。本地 SQLite 升级执行 `.venv/Scripts/python scripts/upgrade_local_tlr.py`（先自动备份，再追加字段和模型配置表）；PostgreSQL 执行 `alembic upgrade head`。当前工作区已升级。

新版数据集是 Dronology SAFA，13 种原始类型、360 个记录、355 条原始关系；223 个包或仅有路径的引用节点不进入检测。SMOS 保留用于完整源码验证。真实执行仍需在模型配置页或 `.env` 提供可调用的向量与对话模型，失败原因保存到运行详情；不要把独立测试模型结果当作真实 TLR 精度。

## 模型配置界面

前端 `#/models` 管理租户隔离的 OpenAI 兼容连接。保存连接时后端读取 `GET {base_url}/models`，展示模型 ID、提供方、目录状态以及本地/远程属性；远程连接只接受 HTTPS，本地回环或私网地址可使用 HTTP。API 密钥用 Fernet 加密保存，读取接口不返回密钥，请求日志也会脱敏。生产环境建议用 `MODEL_CONFIG_ENCRYPTION_KEY` 提供统一 Fernet key；本地未提供时会创建被忽略的 `data/.model-config.key`。

当前可绑定 `TLR 向量化`、`TLR 链接判定`、`架构结构抽取` 三个任务。模型目录可访问只说明 `/models` 正常；任务旁的“实测”会真正调用 `/embeddings` 或 `/chat/completions`。数据库绑定优先于 `.env`，实际选用信息保存在每次 TLR 的 `manifest.model_tasks` 中，密钥不会进入运行记录。

## 现在的 API

新增独立 [TLR 模块](docs/tlr.md)，支持软件制品快照导入、候选检索、LLM 链接判定、
制品级链接聚合、全过程结构化持久化及金标准评测。
运行接口位于 `/api/v1/tlr/`，详见上述模块文档与 Swagger `/docs`。
原有三个接口保持可用。

当前仍沿用原始骨架的接口名，方便先把底座跑起来：

- `POST /api/v1/knowledge-bases/create`
- `POST /api/v1/documents/upload`
- `POST /api/v1/rag/retrieve`

后续建议按软件质量管理语义逐步改成：

- `projects`
- `artifacts`
- `analysis`

## 质量管理下一步

- 接入生命周期文档类型识别
- 接入代码仓库导入
- 建立需求-文档-代码-测试的 trace link
- 做一致性检查与覆盖率分析
- 建立数据集导入、样本标注和评测集管理
