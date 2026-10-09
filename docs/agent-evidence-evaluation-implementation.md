# 历史文档（已废弃）

本文描述的旧 Agent/Evaluation 二次检索路线已由 `0009_review_tools` 移除，不能作为当前运行能力说明。当前信息与审核状态接口见 `docs/information-tools.md`。

# SRS→SDS Requirement Agent Evidence Evaluation 实施报告

日期：2026-09-28

## 1. 重构结论

正式评价链路已经替换为：

```text
completed TLR direct TraceLinks
  → Requirement Evaluation Run
  → Requirement Analyst（只读 SRS）
  → frozen Claims
  → Evidence Investigator ↔ canonical / TLR / BM25 / Dense / pair validation tools
  → Evidence Workspace
  → Conformance Judge ↔ 有上限的补证请求
  → Claim Verdicts
  → deterministic Requirement Assessment
  → fixed Argument Structure + Evidence Acquisition Trail
```

没有修改 Docling、canonical tree、RRF 或 TLR direct link 的语义。没有增加 GraphRAG、实体图、
评分、报告生成或修复建议。

## 2. 原项目真实架构

### 2.1 `software-quality-agent`

- 浏览器：React 19 + TypeScript + Vite。
- Server：Python FastAPI，负责静态前端、旧聊天/报告接口和向质量后端反向代理 `/api/v1/*`。
- 旧 `agents.py` 是项目自定义 Python 类和裸 `httpx` Chat Completions 客户端，并非 OpenAI Agents SDK。
- 重构前 `pyproject.toml` 和虚拟环境均没有 `openai-agents`；不存在 SDK Runner、function tool、handoff、session 或 tracing UI。
- 浏览器不直接访问数据库，但旧 UI 维护了大量手写且已漂移的 API 类型。

### 2.2 `software-quality-management-backend`

- Python FastAPI + SQLAlchemy async；生产目标 PostgreSQL/pgvector，测试使用 SQLite。
- canonical 工件树：`artifacts / content_nodes / content_relations`。
- TLR：`tlr_runs / semantic_annotations / retrieval_projections / trace_units /
  trace_candidates / trace_links`。
- Sparse：`bm25s` + `jieba-py`；Dense：已有 embedding provider + pgvector/本地 SciPy；融合为等权 RRF。
- Fine Validation：已有 `FineTraceValidator`，只把通过 canonical quote 校验的 `direct` pair 写成 TraceLink。
- 任务执行是 API 内的可恢复 stage/run，不存在独立 worker/job queue。
- LLM/embedding 均通过 provider 注入。

## 3. 原 consistency analysis 的真实调用链

重构前唯一仍可见的正式入口是 React `ConsistencyAnalysis.tsx`：

```text
ProjectPage “一致性分析”按钮/Tab
  → /api/v1/consistency/runs
  → 可选 /api/v1/requirements/analyses
  → /api/v1/consistency/runs/{id}/execute
  → ConsistencyExplorer / consistencyModel
```

页面试图支持“从零开始”重新整理 requirement、重新跑 TLR、再生成旧 Report/Aggregation，输出
`implemented / missing_functionality / conflict / judgement_error` 及大量旧 snapshot 字段。

但当前后端源码中已经没有 consistency、requirements、旧 dataset API 的 `.py` 实现，仅有历史
`__pycache__`；当前 FastAPI 只挂载 `/api/v1/traceability`。因此这条 UI→API 正式路径实际会
404，旧 persistence/model/tool 无可部署源码。不能把 `__pycache__` 当作正式实现继续维护。

处理结果：删除 `ConsistencyExplorer.tsx`、`consistencyModel.ts`、`ConstructedStructure.tsx` 和旧
Explorer 样式；重写原入口为 Requirement Agent 证据评价；不再提供“从零开始”或旧 evaluator。
历史二进制缓存没有作为迁移来源。

## 4. 最终部署和模块边界

采用目标形态 B：

```text
React browser
  → software-quality-agent FastAPI same-origin proxy
  → quality backend
      ├ canonical tree / TLR / BM25 / Dense / DB
      └ OpenAI Agents SDK runtime + evaluation orchestrator
```

Agent runtime 放在质量后端，因为 canonical SQLAlchemy session、pgvector、BM25 投影、Fine
Validation 和派生持久化都已在这里。放到 gateway 会制造双份 schema 和细粒度 RPC；合并整个
工程则超出本轮必要范围。浏览器只创建 run、触发执行、查看 Evidence/Argument/Audit。

## 5. OpenAI Agents SDK

- 实际新增：Python `openai-agents==0.22.3`（约束 `>=0.22.3,<0.23`）。
- 使用：`Agent`、`Runner`、`function_tool`、Pydantic `output_type`、typed run context、
  `OpenAIChatCompletionsModel`。
- 未用 handoff：Analyst→Investigator→Judge 顺序稳定，代码 orchestrator 更可控。
- SDK tracing 显式 `tracing_disabled=True`，避免向 OpenAI 发送 trace；另存业务 audit trail。
- 模型 endpoint 必须是 loopback、RFC1918 私网或 Docker 内部名称；公网 endpoint 被拒绝。

## 6. 三个 Agent

| Agent | 输入 | 结构化输出 | 边界 |
|---|---|---|---|
| Requirement Analyst | requirement canonical text、heading path、SRS parent/child context | `RequirementAnalysisOutput`：summary、动态数量 claims、ambiguities | 第一轮无 SDS、无 tools；claim quote 必须逐字存在于 SRS node |
| Evidence Investigator | frozen claims、direct TraceLink 导航摘要、当前 workspace、Judge requests、tool catalog | `InvestigationOutput`：support/counter/unverified selections、information needs、missing evidence、summary | 自主选择工具；正式 quote 必须回到 canonical SDS；新 support 必须 pair verify |
| Conformance Judge | requirement、frozen claims、带验证状态的 workspace、unresolved/missing、剩余轮次 | `JudgeOutput`：每 claim verdict、requirement summary、gaps、evidence requests | 无无限检索；只提出 claim-specific 缺口；后端校验 claim 完整性并确定性合成 requirement verdict |

Verdict 固定为 `supported / contradicted / insufficient_evidence / uncertain`。模型不能用一个
unverified support 得出 `supported`；orchestrator 会降级为 `insufficient_evidence`。

## 7. Tools

| Tool/能力 | 复用能力 | 输入 | 返回类别 | 可作正式 Evidence |
|---|---|---|---|---|
| `get_requirement_context` | `ContentNode` + heading/path + canonical reader | 当前 run context | Evidence（SRS） | 是，SRS 原文 |
| `get_existing_trace_links` | `TraceLink(relation=direct)` + annotation synopsis | 当前 requirement | Navigation | 否；只定位 SDS |
| `read_design_node` | canonical `node_content` + provenance | node ID | Evidence Result | 是，且 quote 必须 substring 校验 |
| `navigate_design_node` | parent/children/sibling + `ContentRelation` | node ID | Navigation | 否 |
| `search_bm25` | 正式 `bm25s`/`jieba-py` raw projection | query、Top-K | Navigation | 否 |
| `search_dense` | 正式 embedding provider + 已有 raw projection vectors/pgvector | query、Top-K | Navigation | 否 |
| `verify_candidate` | 已有 `FineTraceValidator` 和 evidence assembler | 新 node ID | Verification | 只有 direct 才成为 verified support |
| `validate_selection` | canonical reader、quote/provenance 检查 | node、quote、kind | 后端 guardrail | 是，按 verification status 使用 |

没有 Agent 专用 vector index、BM25、classifier、JSON repair、RPC 或模型服务器。

## 8. Investigation 和补证循环

1. Orchestrator 先加载 SRS canonical context 和 direct TraceLink 的轻量导航信息。
2. Investigator 可读取已有 linked node，也可按缺口做结构导航、BM25 或 Dense。
3. 检索结果只含 heading/rank/score/preview；选中后必须读取 canonical text。
4. 非 direct-linked supporting candidate 必须调用 Fine Validation。
5. Workspace 去重并区分 supporting/counter/unverified、verification status、unresolved/missing。
6. Judge 返回完整 claim verdict；如有明确 information need，代码最多再调用 Investigator
   `max_judge_loops` 次。
7. Tool calls、retrieval rounds、同 channel 重复 query、同 node 读取、累计 tool output chars、
   Judge loops 均有配置上限。

审计只保存 Agent 输入边界、结构化输出、Tool 名称/参数、返回 node IDs、验证状态、预算快照、
Evidence Request 和 verdict normalization，不保存 chain-of-thought。

## 9. Argument Structure

后端不调用第四个 Agent，而是按固定 parent 关系生成：

```text
requirement
└─ claim
   ├─ supporting_evidence
   ├─ counter_evidence
   ├─ verdict
   └─ gap
```

只有 Judge 真正引用且通过后端规则的 evidence 才进入 argument tree；未选中的 navigation 或
unverified candidate 留在 workspace/audit，不冒充论据。

## 10. 数据库变更

迁移 `0005_agent_evaluation` 新增：

- `requirement_evaluation_runs`：上游 TLR、配置、stage、metrics；
- `requirement_evaluation_claims`：冻结的动态 claims 和 SRS quote；
- `requirement_evaluation_evidence`：canonical quote、node、provenance/location、kind、验证状态；
- `requirement_claim_verdicts`：claim verdict、实际采用的 support/counter evidence IDs、gaps；
- `requirement_assessments`：无加权的 requirement verdict/summary；
- `requirement_argument_nodes`：固定模式树；
- `requirement_evaluation_audit_events`：可审计过程事件。

这些都是 canonical tree / TLR 的派生数据，不复制整份 SRS/SDS，也不写回 canonical tree。

## 11. 前端变更

- 项目页唯一正式入口改为“Agent 证据评价”。
- 创建页只能选择已完成 TLR；后端进一步强制必须含 direct TraceLinks。
- 展示 Requirement→Claim→canonical Evidence→Verdict→Gap、Argument tree 和 Tool trail。
- 删除旧一致性 Explorer/旧 report model；旧页面不会再请求 `/consistency/*`。
- 模型、数据库、检索与 canonical access 均留在 server。

## 12. 论文与成熟实现

论文原文、方法阅读范围和逐项采用/未采用理由见
`docs/research/agent-evaluation/README.md`。核心来源为 Agent-as-a-Judge、FIRE、Search-o1、
Findeis et al. 和 JSS assurance-case-pattern paper。Agent runtime 依据 OpenAI Agents SDK 官方文档；
BM25、Dense、Fine Validation 复用现有项目实现。

## 13. 新增依赖

唯一新增直接依赖是 `openai-agents>=0.22.3,<0.23`。原因是任务明确要求正式使用 OpenAI
Agents SDK，而原代码只有自定义类和裸 HTTP，无法提供 SDK Runner/function tools/structured
output。其传递依赖由该 SDK 管理；没有新增 Agent framework、向量库、BM25 或 model server。

## 14. Server Local LLM Inventory

2026-09-28 通过交互式 SSH 只读复核：

- GPU：NVIDIA GeForce RTX 4090 D，24564 MiB；已用 11041 MiB，可用 13035 MiB。
- RAM：62 GiB，可用约 51 GiB；根盘 1.8 TiB，可用约 706 GiB。
- 运行中：OvisOCR2 vLLM（约 8588 MiB）、Qwen3 embedding vLLM（约 2332 MiB），均 healthy。
- 停止容器：`qwen35-9b`，exit 0、非 OOM；vLLM，`Qwen/Qwen3.5-9B`，BF16 历史配置，
  `max-model-len=32768`、`gpu-memory-utilization=0.90`、Qwen3 reasoning/tool parsers。
- 权重：`/home/fuxin/.cache/huggingface/hub/models--Qwen--Qwen3.5-9B/...`，约 19 GiB，
  4 个 safetensors shard、config/tokenizer/index 均存在。
- 常见 cache/旧目录没有发现第二个完整生成式 LLM；embedding/OCR 不算生成式 LLM。

结论：**本地生成模型权重存在，但当前无法安全并行启动。** 历史加载需约 16.8 GiB GPU，
实时仅余约 13.0 GiB；启动 0.90 配置必然与现有 embedding/OCR 争抢显存。按“不停现有
embedding/OCR、不覆盖旧部署”的约束，没有启动容器，也没有做中文/structured/tool/context/
concurrency smoke。没有调用 DeepSeek/OpenAI/公网 API，没有下载模型。

## 15. 测试结果

- 后端：`25 passed`，包含新的 fake E2E wiring：direct TraceLink→Claim→读取 canonical SDS→
  verified evidence→Judge evidence request→第二轮 investigation→supported verdict→argument/audit。
- 预算测试：重复 query、最大 tool calls 均确定性终止。
- 数据库：空 SQLite 从 0001 升至 `0005_agent_evaluation` 成功，7 张新表存在。
- 前端：`tsc --noEmit && vite build` 成功。
- 静态检查：Ruff 全部通过。
- 指定文件存在：
  - `datasets/test/-软件需求规格说明(V2.7.3)---GJB438C(1).docx`
  - `datasets/test/DT软件设计说明(V4.0）(1).docx`

当前本地正式数据库没有这两份文档对应 Artifact/TLR（`artifacts/content_nodes/tlr_runs/trace_links`
均为 0），服务器模型又不能安全启动，因此没有伪造真实 Agent 输出。

**Local generative LLM unavailable for safe concurrent serving. Real agent evaluation was not executed.**

## 16. 失败案例与定位

1. **服务器模型 smoke 未执行：运行时资源问题（J / L）**。模型权重完整，但 16.8 GiB 历史
   需求高于 13.0 GiB 实时空闲显存；不是 Prompt 或 Agent 代码错误。
2. **旧一致性页面不可运行：API/schema implementation error（K）**。前端请求的 consistency/
   dataset endpoints 在当前后端源码不存在，不是评价结论错误；已移除该正式调用链。
3. **真实 SRS/SDS evaluation 缺失：initial data/TLR prerequisite**。文件存在，但当前新数据库无
   corresponding Artifact/direct TraceLinks；本流程按要求不绕过 TLR 重建事实。

## 17. Remaining Problems / UNRESOLVED DESIGN DECISIONS

- `UNRESOLVED DESIGN DECISION — counter-evidence verification`：现有 FineTraceValidator 只判断
  direct/related/no_link/uncertain，没有 contradiction taxonomy。查阅上述论文后仍没有与当前
  TLR schema 直接兼容的成熟实现。当前最保守处理是 canonical exact quote + `canonical_only`，
  由 Judge 显式引用，不写 TraceLink、不升级为 verified support。
- 模型行为、JSON 稳定性、tool calling、32K context 和并发均未在 Qwen3.5-9B 上实测。
- API 执行仍是同步请求，沿用现有 TLR run 方式；大批量任务后续应复用成熟 job queue，而不是
  自建 workflow engine。
- 前后端总体 API contract 仍有历史漂移；本轮 evaluation client 兼容 raw/envelope response，
  但没有在未统一全部旧 API 前贸然引入 OpenAPI code generation。
- 没有人工 ground truth，预算、Top-K、Claim 粒度和 verdict recall/precision 不能声称最优。
- `software-quality-agent/src/software_quality_agent/agents.py` 中旧聊天/报告自定义 Agent 类仍存在，
  但不在本次 Requirement evaluation 正式路径；若这些通用功能确认不用，可另行删除。
- 前端旧 TLR/dataset UI 与当前精简后端的其他历史 contract 不一致，本轮只替换了重叠的正式
  consistency 入口；应作为独立 API 收敛任务处理。
