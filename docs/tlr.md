# TLR 模块：输入、执行与持久化

新增分层矩阵、类型分割与结构字段以 [tlr-layers-and-structure.md](tlr-layers-and-structure.md) 为准。迁移 20260906_0004 在既有表追加 structure / processing，不替换旧结果。

模块位置：`app/modules/tlr/`。作用是对同一软件的两组制品执行有方向的追踪链接恢复（TLR），例如需求到代码、需求到测试、设计到代码。当前输出表示 `related_to` 关系，不直接证明实现正确、测试执行成功或需求已经完全覆盖。

## 基线与复用范围

- 论文：Fuchß 等，*LiSSA: Toward Generic Traceability Link Recovery through Retrieval-Augmented Generation*，ICSE 2025，重点参照 III.A–C 和图 5。
- [官方复制包](https://github.com/ArDoCo/Replication-Package-ICSE25_LiSSA-Toward-Generic-Traceability-Link-Recovery-through-RAG)，固定提交 `a8e652f29bbdcc4bdcd3d98cd85aa4a37cb65480`，选择 `LiSSA-RATLR-V2/lissa`。
- `Evaluation.java` 提供编排基线，`ElementStore.java` 提供余弦 Top-k 检索，`AnyResultAggregator.java` 提供任一正向元素对形成制品链接的规则。
- `retrieval_backend=lissa` 通过 Java 桥接直接调用原版 `ElementStore.findSimilarWithDistances`；`python` 使用相同算法的后端适配，默认无需 Java。Java 使用 float，Python 使用双精度；相近分数可能产生排序差异。
- 其他阶段为后端适配：复用已有 embedding/LLM provider、严格 JSON 布尔判定与原文证据、SQLAlchemy 持久化。没有声称复现论文全部算法配置或效果。
- 论文有制品/句子/代码方法/语言相关分块/UML 特征等预处理。当前仅支持完整制品和无重叠字符分块；不静默截断过长制品。关系判定采用 KISS 思路并增加简短证据输出，未实现论文 CoT 提示。
- 原版主要输出结果 CSV/评测文件，未提供本项目所需的租户、软件版本、HTTP 服务和关系型运行证据模型，因此新增七张独立表。

## 安装与迁移

在后端根目录运行：

```powershell
python -m pip install -e ".[dev]"
# 如需原版 Java 检索，需 JDK 21 与 Maven 3；下载与构建均写入项目目录
.\scripts\build_lissa.ps1
# 配好 .env 的数据库、embedding 和 LLM 接口后：
alembic upgrade head
uvicorn app.main:app --reload
```

原版源码保存于 `vendor/lissa/`，固定版本信息在 `integrations/lissa/dependency.json`，逐文件 Git blob SHA-1、SHA-256 清单在 `artifacts/lissa/dependency-manifest.json`。下载脚本仅选择 V2 源码、构建文件、SMOS 和许可，不需要复制包的大型评测缓存。Java 构建产物为 `vendor/lissa/LiSSA-RATLR-V2/lissa/target/ratlr-0.2.0-SNAPSHOT-jar-with-dependencies.jar`，桥接 class 在 `artifacts/lissa/bridge/`。源码不通过 pip 安装，也没有修改上游源码。

常规 Docker 镜像仍是 Python 镜像，默认 `python` 检索可用；选择 `lissa` 时需要额外提供 Java 21、JAR 和桥接 class，并配置 `TLR_LISSA_JAR` 与 `TLR_LISSA_BRIDGE_DIR`。此环境未验证 Docker 部署。

## 支持输入

`POST /api/v1/tlr/datasets` 接受已抽取正文的 JSON，示例：

```json
{
  "tenant_id": "local",
  "project_id": "example-software",
  "version": "v1",
  "provenance": {"source": "repository or document collection", "commit": "optional"},
  "artifacts": [
    {"external_id": "REQ-1", "kind": "requirement", "revision": "v1", "content": "系统应支持用户登录。", "locator": "requirements.md#REQ-1"},
    {"external_id": "CODE-1", "kind": "code", "revision": "v1", "content": "def login(username, password): ...", "locator": "src/auth.py"}
  ]
}
```

`kind` 是可扩展的非空字符串，可写 `requirement`、`design`、`interface`、`code`、`test`、`defect`、`release`、`operation`、`feedback`、`configuration`、`change` 等。这些是业务标签，不代表已经内置相应文件格式解析器。PDF、DOCX、源码仓库、架构模型需要先提取为文本；本模块不会直接打开上传文件、Git URL 或执行待分析代码。旧 `/documents/upload` 的切片不会自动成为 TLR 制品，需调用方转换为显式快照。

已支持 TraceLab XML 的 `internal` 正文和 `external` 文件引用导入，禁止外部路径越界并检查正文存在。现有 Agent 目录中的 SMOS XML 引用正文文件，但未带齐这些文件；本次下载补齐到了后端 `vendor/lissa/.../SMOS/UC` 与 `CC`。`answer.csv` 无表头，第一行也是金标准链接。

每个快照必须有至少两个制品，`external_id` 在快照内唯一。运行的 source/target 集合必须非空、不重复、不重叠，且属于该快照。对同一软件的不同版本，请分别导入快照；`project_id` 是调用方提供的作用域标识，不是已新增的项目主数据管理模块。

## 运行与导出

```powershell
python -m scripts.prepare_smos
# 生成 artifacts/tlr/smos/{dataset.json,gold.json,run-template.json}
python -m scripts.run_tlr --dataset artifacts/tlr/smos/dataset.json --run artifacts/tlr/smos/run-template.json --gold artifacts/tlr/smos/gold.json
```

脚本会导入快照、自动填充 dataset_id、新建并执行运行、可选评测，并把 JSON 保存到 `outputs/tlr/<run_id>/`。使用自己的软件时，按同样契约提供 dataset 与 run JSON。脚本调用已启动的后端；真实执行需要配置 `MODEL_API_KEY` 和 `LLM_API_KEY`。`--gold` 只送到评测接口，不进入模型输入。SMOS 模板默认 top-k=20、需求整件、代码按 4000 字符分块；这是工程示例配置，不是论文效果复现实验。

运行 JSON 示例：

```json
{
  "tenant_id": "local",
  "project_id": "example-software",
  "dataset_id": "替换为导入返回的 id；run_tlr 脚本会自动填充",
  "source_ids": ["REQ-1"],
  "target_ids": ["CODE-1"],
  "options": {"top_k": 20, "source_preprocessor": "artifact", "target_preprocessor": "chunk", "chunk_size": 2000, "retrieval_backend": "lissa"}
}
```

API 均返回 `code/msg/data`：

| API | 用途 |
|---|---|
| `POST /api/v1/tlr/datasets` | 保存不可变制品快照 |
| `GET /api/v1/tlr/datasets/{id}` | 查询快照及来源 |
| `GET /api/v1/tlr/datasets/{id}/artifacts` | 分页查询原始制品正文 |
| `POST /api/v1/tlr/runs` | 校验两组制品并建立 pending 运行 |
| `POST /api/v1/tlr/runs/{id}/execute` | 同步执行已创建运行 |
| `GET /api/v1/tlr/runs/{id}` | 查看阶段、配置、模型标识、数量及失败类型 |
| `GET /api/v1/tlr/runs/{id}/outputs/{kind}` | 分页查询 elements / candidates / links / evaluations |
| `POST /api/v1/tlr/runs/{id}/evaluations` | 输入独立金标准，保存 P/R/F1 与候选召回率 |

路径查询/执行接口均需 `tenant_id`、`project_id` 查询参数。分页参数 `offset>=0`、`1<=limit<=500`。作用域检查沿用本项目调用方传 tenant 的约定；这不是身份认证，生产环境仍需接入可信身份上下文。

## 保存的数据

### 项目资料库与文件上传

项目归属 `tenant_id/project_id` 和业务类型 `kind` 均为外部必填输入，不进行内部推断。前端以当前项目与逐文件类型选择提交这些字段。单文件也可建快照；检测必须选择互不重叠的源和目标。

迁移 `20260905_0003_project_files` 新增 `tlr_projects` 和 `tlr_files`，回填历史项目，给制品增加 `original_file_id`。原文件字节、名称、媒体类型和摘要保存到数据库。上传基于旧快照创建新快照，明确允许才替换新快照中的同标识制品，旧数据保留；整批校验后原子保存。

新增 API（前缀 `/api/v1/tlr`）：

| API | 用途 |
|---|---|
| `GET /capabilities` | 类型、扩展名、上传限制和模型配置状态 |
| `GET/POST /projects` | 分页项目目录 / 新建项目 |
| `GET /projects/{project_id}` | 项目信息 |
| `GET /projects/{project_id}/datasets` 或 `/runs` | 项目快照 / 历史运行 |
| `POST /projects/{project_id}/upload` | multipart 上传 |
| `GET /datasets/{id}/inventory` | 不含正文的分页清单 |
| `GET /artifacts/{id}` 或 `/download` | 单份正文记录 / 原始字节（纯文本导入则下载正文） |
| `GET /runs/{id}/visualization` | 单元索引、候选摘要和链接，不含向量/正文 |
| `GET /runs/{id}/candidates/{candidate_id}` | 判定证据与源/目标单元全文 |

项目路径接口需 tenant_id，其他对象接口需 tenant_id 与 project_id。目录 limit 最大 100。上传需 tenant_id、version、files、metadata；metadata 为与 files 同序的 `[{"external_id":"R1","kind":"requirement","revision":"v1"}]`，可选 base_dataset_id、replace_existing。类型见 `uploads.py:KINDS`，包括生命周期文档、code、test_case、test_code。支持 UTF-8 文本代码、PDF 文本和 DOCX 段落/表格，不做 OCR。单文件 10 MiB，单批 50 件/50 MiB，正文最多 500000 字符；非法格式或超限返回 422。下载返回附件字节，其他接口返回统一 JSON。

`catalog.py` 管理项目查询、上传事务和结果视图，`uploads.py` 管理解析；前端不提供任意数据库写入。

| 表 | 数据与含义 |
|---|---|
| `tlr_datasets` | 快照 ID、tenant/project/version、内容摘要、来源和创建时间 |
| `tlr_artifacts` | 快照内制品 ID、外部标识、kind、revision、locator、完整正文、SHA-256 |
| `tlr_runs` | 运行 ID、快照、所选两组制品、options、status/stage、manifest、counts、error、时间 |
| `tlr_elements` | 制品对应处理单元，source/target、ordinal、字符区间 `[start,end)`、正文与摘要、embedding 数组 |
| `tlr_candidates` | 每个源单元的 Top-k 目标、rank、余弦 similarity、pending/related/unrelated、结构化证据 |
| `tlr_links` | 制品对的 `related_to` 链接和支撑它的候选 ID 列表；任一个正向元素对即可建立链接 |
| `tlr_evaluations` | 独立金标准、来源、precision/recall/F1、候选召回率、未召回金标准链接数 |

生产保存位置由 `DATABASE_URL` 指定，默认 PostgreSQL。迁移 `20260905_0002_tlr` 在原迁移上新增七张表。TLR 向量保存为 JSON 数组，检索时加载到内存；没有复用旧 `chunks` 表的 pgvector 索引，避免隐式混用知识库范围或固定向量维度。当前实现适用于受限批次，不是大型向量索引服务。

正常失败会保存 failed 状态、失败阶段/异常类型和已完成步骤。每批 embedding、每个候选判定单独提交；非法模型结构/不支持的引文保留原始 JSON 以核对，但候选仍为 pending。最终链接在全部判定完成后一次提交。失败结果不能用于最终评测。重复 execute 返回 409；重试需新建运行，旧证据保留。

硬终止进程或数据库持续不可用时，无法保证写入 failed 状态；可能留下 running 记录，需人工排查后创建新运行。目前没有持久任务队列、自动恢复、人工复核流或自动重试。余弦相似度不是链接概率；没有被召回的配对也不等于不相关。`max_element_chars` 超限会失败，提示改用 chunk，不会静默裁剪。

## 协作分工与验证

`schemas.py` 负责契约，`models.py`/迁移负责存储，`importers.py` 负责数据接入，`pipeline.py` 负责预处理/检索/判定适配，`service.py` 负责阶段编排和评测，`repository.py` 负责查询，`router.py` 负责 HTTP。扩展新制品解析器、新预处理器、新模型或新检索器时，优先在相应边界扩展并补充基线来源。

```powershell
python -m pytest -q --basetemp artifacts/pytest-tlr-local
python -m ruff check app/modules/tlr scripts tests/test_tlr.py
```

当前已验证数据库迁移的升/降级与 ORM 一致性、SQLite 文件库的 API 持久化与隔离、失败后保留候选、聚合/评测口径，以及真实编译的 Java 检索桥接。未配置真实 embedding/LLM 密钥，未执行 SMOS 模型效果评测；也未对运行中的 PostgreSQL 实例应用迁移。
