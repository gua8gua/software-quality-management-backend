# SRS→SDS TLR 正式管线修复报告（2026-09-28）

> 后续变更：reranker 已从正式路线绕过，公网 DeepSeek 不再是默认 LLM；校园服务器已确认存在完整本地 Qwen3.5-9B，但因当前显存冲突未启动。当前结论以 `docs/local-llm-inventory-and-hybrid-baseline.md` 为准；下文保留为上一轮实现历史。

## A. 修复内容

- 修复 canonical tree 的 section attachment：Docling DOCX 常把正文块直接 parent 到 `#/body`；现在保留真实 group parent，但 body-level 内容按 reading order 挂到活动 section stack。未用正文关键词猜标题。
- role 与 synopsis 拆成两次结构化调用；role 是正式 TLR 必需结果，synopsis 失败写入 `synopsis_status=failed` / `synopsis_error`，不阻断 raw retrieval。
- 删除 min-rank 排序，使用标准无权 RRF：`Σ 1/(k+rank_i)`，默认 `k=60`。默认通道仅 raw dense + BM25；实验开关才加入等权 synopsis dense。
- Candidate 持久化 dense/BM25/可选 synopsis 的 rank/score、`rrf_score`、`fused_rank`、reranker rank/score 和 retrieval source。
- RRF Top-N 是结构扩展 seed；一跳 sibling/relation 若有自己的 TraceUnit，就作为独立 candidate 独立 rerank/fine validate。EvidencePackage 中的 context 只作支持，不改变 target。
- direct 必须至少有一条 exact-substring evidence 来自 candidate target 自身。support-only direct 会 constrained retry 一次；仍违反则降级 uncertain，并记录 `direct_target_evidence_missing`（以及适用时 `direct_evidence_found_in_support_only`）。
- 只有 direct 创建 TraceLink；related/uncertain/no_link 只保留在 TraceCandidate。
- picture context 显式传递 `visual_content_unparsed`，picture 禁止作为 textual direct evidence；未增加 OCR/VLM。
- embedding/reranker 增加官方 tokenizer preflight；Qwen reranker 已移除 `longest_first` 静默截断。超限显式失败并给出索引与限制。
- OpenAI-compatible LLM 对 timeout、transport、429 和 5xx 使用 Tenacity 最多三次退避重试；schema 错误只在 fine direct target evidence 场景 constrained retry 一次。
- run 分为 Annotation、Projection/Embedding、Retrieval/RRF/Rerank、Fine Validation 四个 checkpoint。annotation 每 50 条提交，fine 每个 candidate 提交；failed run 可复用最近已提交阶段。
- run 非 completed 时，links API 不暴露该 run 的 TraceLink；重启 completed/pending run 会先在独立事务中失效旧派生结果。
- 新增 Alembic `0004_tlr_reliability_and_rrf`；SQLite fresh upgrade 已到 head。

## B. Heading hierarchy 根因与真实修复

根因在 canonical tree，不在 `heading_path()`。旧 `nearest_mapped_parent()` 沿 Docling parent 首先命中 canonical document root，导致后面的 section-stack fallback 永远不可达；`heading_path()` 只是正确地反映了错误 parent 链。

修复后，body-level paragraph/table/group 在 body layer 内连接到活动 section；真实 Docling group 仍优先保留；furniture 不继承 body section。SRS/SDS 共输出 23 个分层类别覆盖样例，详见：

- `outputs/traceability/heading-path-before-after.json`
- `outputs/traceability/heading-path-before-after.md`

示例：

- `CO-6: MQTT通信协议的服务器采用EMQX Broker 4.5版本。`
  - old：文档根
  - new：文档根 > `3 需求分析` > `3.10 设计和实现的约束`
- `SAF-1: 权限控制...`
  - old：文档根
  - new：文档根 > `3 需求分析` > `3.9 安全及保密要求`
- SDS 总体设计正文
  - old：文档根
  - new：文档根 > `4 系统总体设计` > `4.1 数字孪生系统集成平台`

覆盖类别包括 SRS 用例、功能、性能、数据、安全、实现约束、质量属性，以及 SDS 总体设计、详细设计、数据库、页面和第 3 章具体技术方案。

## C. Hybrid research

实际下载、全文抽取并阅读：

1. Cormack, Clarke, Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and Individual Rank Learning Methods*，SIGIR 2009。采用其公式、rank-only 设计、`k=60` pilot 依据和避免未校准 raw score 的原则。
2. *IIMAS-RAG at SemEval-2026 Task 8*，2026。复核 sparse+dense 等权 RRF (`k=60`)；该论文的 reranker 在其数据上降分并从最终提交排除，未把它误述为最终三阶段方案。
3. Caraman, *SemEval-2026 Task 8: Three-Stage Multi-Turn Retrieval with Query Rewriting, Hybrid Search, and Cross-Encoder Reranking*，2026。其正式管线是 BM25S + dense → 无权 RRF (`k=60`) → cross-encoder，直接支持本项目阶段顺序。

PDF、全文抽取和对应实现说明保存在 `docs/research/hybrid-retrieval/`。

## D. RRF 实现

`RRF(d) = Σ_i 1/(60 + rank_i(d))`

- baseline 通道：raw dense、BM25；
- 实验通道：可选 synopsis dense，仍等权；
- raw score 不归一化、不相加，仅持久化诊断；
- 每个 source query 内独立计算 fused rank；
- RRF Top-20（可配置）作为 reranker seed；结构一跳扩展节点作为独立候选加入该 source 的 rerank batch；
- reranker Top-5（可配置）进入 fine validation。20/5 只是 baseline default，不宣称最优。

## E. Server inventory

用户补充凭据后，已通过交互式 SSH 成功连接 `fuxin@10.71.29.93`；密码没有写入源码、`.env`、shell 命令、日志或本报告。

- OS：Ubuntu 22.04.4 LTS；内存 62 GiB，盘点时约 51 GiB available。
- GPU：NVIDIA GeForce RTX 4090 D，24564 MiB；盘点时 used 11041 MiB、free 13035 MiB。
- 根磁盘：1.8 TiB，总使用 1014 GiB，空闲 726 GiB（59%）。
- 现有部署：`/home/fuxin/software-quality-deployment/backend`；现有模型编排：`/home/fuxin/wy/model-service/docker-compose.yml`，模型目录约 2.8 GiB。
- 运行容器包括 backend API（8000）、frontend（5173）、PostgreSQL/pgvector、Qwen3 embedding（18080）和 OvisOCR2（17860）。停止状态中另有旧的 Qwen3.5-9B 容器。
- 已有 LLM：线上后端配置为 DeepSeek OpenAI-compatible `deepseek-chat`，但最小真实调用返回 HTTP 401，当前凭据无效。
- 已有 embedding：vLLM 0.22.1，`qwen3-embedding-0.6b-1024`，服务侧最大长度 4096，真实 `/tokenize` 与 `/v1/embeddings` 均成功。
- 已有 reranker：无运行服务，也无本地 Qwen3-Reranker 权重缓存。

没有覆盖旧部署或修改现有 compose。曾启动一个命名明确的一次性 reranker smoke 容器；因服务器访问 Hugging Face 返回 `Network is unreachable`，在模型加载前停止并删除。线上 embedding/OCR/backend 容器没有停止或重启。

## F. Model runtime

- LLM：配置模型 `deepseek-chat`，真实调用返回 401；未用于 TLR 结果生成。
- Embedding：已复用服务器的 vLLM 0.22.1 + `qwen3-embedding-0.6b-1024`。真实 smoke 返回 1 个 1024 维向量；同一服务的 `/tokenize` 返回测试输入 20 tokens、`max_model_len=4096`。
- Reranker：目标仍为 `Qwen/Qwen3-Reranker-0.6B` 官方 Transformers yes/no recipe；因服务器无缓存且无 Hugging Face 出网，未成功加载。
- 新增 `OpenAICompatibleEmbeddingProvider`，直接复用 `/v1/embeddings`，并在推理前调用 vLLM 官方 `/tokenize` 获取准确 token count 与服务端 `max_model_len`；超限仍显式失败，不用客户端字符估算或静默截断。
- 本机此前只下载 Qwen 官方 tokenizer用于真实文档长度统计；没有把本机 fake provider 结果当作真实模型结果。

依据：[Qwen3-Embedding 官方仓库](https://github.com/QwenLM/Qwen3-Embedding) 的 query instruction 格式与 embedding/reranker recipe；[vLLM OpenAI-compatible server 文档](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html) 的 `/tokenize` API。Qwen 官方提供可直接加载的 reranker 实现，因此没有自行设计新评分算法或自建平行模型服务。

## G. Real document run

真实解析文件：

- SRS：727 nodes（415 paragraph、90 table、48 picture、122 section、51 group、1 document）。
- SDS：2353 nodes（1335 paragraph、109 table、459 picture、140 section、309 group、1 document）。
- Docling 非空文本静默丢失：两份均为 0。
- 结构化表：199；官方 serializer 错误：0。
- picture：507；模型图片解析调用：0。
- hierarchy 修复后所有非根节点都有 parent，reading order 连续。

官方 tokenizer 的真实 projection 统计：

- paragraph 1750：embedding tokens p50=81、p95=218、max=470；
- table 199：embedding tokens p50=301、p95=1053、max=2470；
- embedding 32K 超限：0；
- reranker node-only（含官方 prefix/suffix、空 query）8192 超限：0；实际运行仍对完整 query+document pair 做 preflight。

真实文档结构解析、heading 修复、projection/token 统计继续使用且只使用用户指定的 SRS/SDS 两份文件。由于 LLM 401 且 reranker 权重无法取得，没有启动不完整的全流程实验，也没有生成 role、dense/RRF/reranker/fine 分布或真实 direct links；fake provider 结果仅用于代码 E2E，不冒充真实质量结果。

## H. 真实案例

`heading-path-before-after.md` 给出 23 个真实节点及 old/new path。真实 BM25/projection 诊断保存在 `outputs/traceability/real-srs-sds-foundation.json`；它覆盖 MQTT、Redis、数据库、故障检测等可人工核对查询。

要求的 10 个“Dense→BM25→RRF→Reranker→Fine→Final Link”真实模型案例无法在当前无模型条件下诚实产出，故没有用 fake provider 伪造。部署条件具备后可直接运行同一正式管线补充该部分。

## I. Remaining problems

- DeepSeek 现有 API 凭据返回 401；需要更新有效凭据后才能执行 Semantic Annotation 与 Fine Validation。
- 服务器无法访问 Hugging Face，且没有 Qwen3-Reranker-0.6B 本地缓存；需由运维离线导入官方权重或恢复受控模型源访问后再做真实 reranker/full-run。
- 尚无人工 ground-truth trace set，不能评价 Recall@20、Recall@5 或 20/5 是否最优。
- reranker pair 超限在真实运行时会明确失败；当前真实 nodes 单体均未超限，未引入未经验证的 chunking。
- picture 继续保留 canonical node 与关系但视觉内容未解析；只依赖图片的案例保持 uncertain。
- migration 0001–0003 是历史事实，包含已被 0004 淘汰的列/约束定义，不应回改；生产库应执行 0004。
- `outputs/segmentation-before-heading-fix/` 是本次根因证据，可在验收后归档；不是运行时依赖。

## 验证

- `pytest`：25 tests passed（新增 remote embedding batch/instruction/tokenize/oversize 测试）。
- Ruff：通过。
- Python compileall：通过。
- Alembic fresh SQLite upgrade：`0004_tlr_reliability_and_rrf (head)`。
- 服务器 embedding + tokenize：真实通过；LLM：真实 401；reranker：模型源网络不可达，隔离容器已清理。按用户允许项未继续部署或执行不完整的 TLR 全流程。
