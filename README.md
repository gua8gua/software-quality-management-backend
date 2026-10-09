# Software Quality Management Backend

后端只保留一条工件处理与追溯主线：

```text
File → Artifact → Parser → segmentation canonical content tree
     → SemanticAnnotation(role + recoverable synopsis) → raw RetrievalProjection
     → Qwen3 dense + bm25s → unweighted RRF(k=60) → configurable Top-K
     → one-hop structure expansion → structured evidence → local LLM pair validation
     → accepted direct pair-level TraceLink
     → TraceLink and run diagnostics
     → information-access tools → optional persisted requirement review state
```

The removed requirement-evaluation/repair Agent pipeline is not part of the
runtime. Information tools are deterministic, scoped, paged reads over the
canonical tree and the single formal TLR run; review tools persist only explicit
review facts and never create another retrieval run or TraceLink set.

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:DATABASE_URL="sqlite+aiosqlite:///data/traceability.sqlite"
python scripts/init_db.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

需要在本机执行 Qwen3 embedding 时，另行安装模型运行时并准备模型权重：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[models]"
```

基础安装不会自动下载模型。

正式 TLR 路线当前不调用 reranker/cross-encoder。`fine_top_k` 表示每个 SRS query
按等权 RRF 选出的种子数；这些种子的一跳独立结构候选也分别进入 Fine Validation。
它是可调 baseline 参数，不代表已经在人工 ground truth 上取得最优值。

LLM 默认关闭，且默认地址只指向 loopback。确认本地生成模型服务已经独立启动后才配置：

```text
LLM_ENABLED=true
LLM_BASE_URL=http://local-model-host:port/v1
LLM_MODEL=<locally served model name>
LLM_API_KEY=
# Qwen3.5 on vLLM: use the official non-thinking request switch so bounded
# structured-output calls do not spend their whole token budget on reasoning.
LLM_CHAT_TEMPLATE_ENABLE_THINKING=false
```

项目不会在本地服务缺失时自动回退到公网商业 API。

当前后端不运行 Agent 编排或云端 tracing；如启用 TLR 生成模型，仅允许通过受控的本地/私网兼容端点配置。
信息访问 Tool 只返回有界业务数据，审核 Tool 将明确提交的审核事实写入本项目数据库。

如需复用已有的 vLLM/OpenAI-compatible embedding 服务，可配置：

```text
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=http://model-host:port/v1
EMBEDDING_API_KEY=<runtime secret>
EMBEDDING_MODEL=<served model name>
```

该 adapter 会先调用 vLLM `/tokenize` 做精确长度预检，再调用 `/v1/embeddings`；密钥只应在运行环境注入，不要提交到 `.env`。

如需调用 Ollama 的原生 embedding API，可配置：

```text
EMBEDDING_PROVIDER=ollama
EMBEDDING_BASE_URL=http://ollama-host:11434
EMBEDDING_API_KEY=
EMBEDDING_MODEL=qwen3-embedding:4b
EMBEDDING_REVISION=<resolved Ollama model digest>
```

Ollama adapter 调用 `/api/embed`，查询侧沿用正式 TLR 的英文 task instruction，
文档侧保持 canonical raw projection。每次请求固定发送 `truncate=false`；Ollama 没有与
vLLM `/tokenize` 等价的预检接口，因此超长输入由 provider 明确拒绝，不允许静默截断。
响应中的向量数量、维度、数值有限性以及跨 batch 维度一致性都会在写入前校验。

数据库迁移：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

生产环境使用 PostgreSQL。项目不再提供旧 Document/Chunk/RAG、旧 TLR preprocessing、Java LiSSA 或旧数据集导入兼容层。

## API

- `GET / POST /api/v1/projects`
- `GET / DELETE /api/v1/projects/{project_id}`
- `GET / POST /api/v1/projects/{project_id}/folders`
- `DELETE /api/v1/projects/{project_id}/folders/{folder_id}`（仅空目录）
- `GET / POST /api/v1/projects/{project_id}/artifacts`
- `GET /api/v1/projects/{project_id}/artifacts/{artifact_id}/original`（保存的原始文件字节及真实 MIME）
- `GET /api/v1/projects/{project_id}/artifacts/{artifact_id}/download`
- `DELETE /api/v1/projects/{project_id}/artifacts/{artifact_id}`（同时清除本项目旧文件快照的派生分析）
- `POST /api/v1/traceability/runs`
- `POST /api/v1/traceability/runs/{run_id}/execute`
- `GET /api/v1/traceability/runs/{run_id}/units`
- `GET /api/v1/traceability/runs/{run_id}/annotations`
- `GET /api/v1/traceability/runs/{run_id}/projections`
- `GET /api/v1/traceability/runs/{run_id}/candidates`
- `GET /api/v1/traceability/runs/{run_id}/links`
- `GET /api/v1/traceability/runs/{run_id}/diagnostics?source_node_id=...`
- `GET /api/v1/projects/{project_id}/information/artifacts`
- `GET /api/v1/projects/{project_id}/information/nodes/{node_id}`
- `GET /api/v1/projects/{project_id}/information/tlr-runs/{run_id}/nodes/{node_id}/results`
- `POST /api/v1/projects/{project_id}/reviews`

旧 Requirement Evaluation 二次检索/obligation 管线已移除。信息访问和审核状态接口见
[`docs/information-tools.md`](docs/information-tools.md)。这不是 Agent SDK 运行时；尚未进行新的
Agent 编排。迁移 `0009_review_tools` 会删除旧评价/修复表及其历史数据，执行前应自行归档。

管理界面只展示文本与代码，文件夹共用一棵项目目录树。上传时 `metadata` 是 JSON 字符串，包含 `external_id`、`modality` 和可选 `folder_id`。文本必须指定 `engineering_role=requirement/design`，代码的 `engineering_role` 必须为空。原始文件由 `File` 保存，目录由 `Folder.parent_id` 保存；不保存旧 `participation/locator/tags/inline_content` 副本。

`scripts/init_db.py` 仅用于配置指向的空数据库，建立当前全部模型并标记 Alembic head；已有数据库只能执行升级。`0012_project_library` 是破坏性升级：删除含不支持类型的项目及关联数据，移除旧字段，并加入数据库类型约束。执行前应备份；不支持降级或旧类型适配。管理页面已移除所有分析入口；独立 TLR 和 SDK Agent 服务仍保留。

详情页不读取 canonical tree 或内容 JSON，只读取 `File.payload` 原始文件接口。`File.media_type/file_format` 由 filetype 文件签名检测确定，不信任上传请求的 MIME，也不根据需求/设计角色猜测。`0013_original_file_types` 对已存原文件回填真实格式和 MIME；旧 DOC 明确标为 `application/msword`。canonical `content_nodes.content_json` 的表格结构、图片对象和 provenance 被 TLR/信息查询使用，不是预览副本，不能删除。
