# 本地 LLM 盘点与 Hybrid Retrieval baseline（2026-09-28）

## 结论

校园服务器存在真正本地的生成式 LLM：`Qwen/Qwen3.5-9B`。完整权重、tokenizer、配置、vLLM Docker image 和已停止的旧容器均在服务器本机；历史日志证明该容器曾成功加载模型并返回 `/v1/chat/completions` 200。

本轮没有启动模型，也没有进行生成、Semantic Annotation、Fine Trace Validation 或完整 TLR 测试。原因是现有 embedding/OCR 已使用 11041 MiB 显存，只余 13035 MiB，而 Qwen3.5-9B 历史加载权重本身使用 16.8 GiB；直接恢复旧容器会与现有服务争抢显存。没有调用 DeepSeek/OpenAI 等外部 API，也没有下载新模型。

## 服务器 inventory

| 项目 | 结果 |
|---|---|
| OS | Ubuntu 22.04.4 LTS |
| GPU | NVIDIA GeForce RTX 4090 D，24564 MiB |
| 当前 GPU | used 11041 MiB，free 13035 MiB，盘点时 utilization 0% |
| 内存 | 62 GiB，总 available 约 51 GiB |
| 根磁盘 | 1.8 TiB，总空闲 726 GiB，使用率 59% |
| 当前生成式 LLM 服务 | 无 |
| 当前 embedding | vLLM 0.22.1，`qwen3-embedding-0.6b-1024`，本地 `/models/qwen3-embedding-0.6b` |
| 当前 OCR | vLLM 0.22.1，`ATH-MaaS/OvisOCR2`；不计为生成式 LLM |
| Ollama / llama.cpp / host vLLM | 未发现 host service/binary |

## 本地生成式 LLM 候选

| 模型 | 参数规模 | 本地路径 | 当前状态 | 推理框架 | 上下文 | GPU 需求证据 | 中文 SRS/SDS 适用性 |
|---|---:|---|---|---|---:|---|---|
| Qwen3.5-9B | 9B | `/home/fuxin/.cache/huggingface/hub/models--Qwen--Qwen3.5-9B/snapshots/c202236235762e1c871ad0ccb60c8ee5ba337b9a` | 完整、停止 | vLLM 0.25.0 Docker | 模型配置 262144；旧 serving 限制 32768 | 历史日志：BF16 权重加载 16.8 GiB，旧配置 GPU utilization 0.90 | 候选合适；仍需以后做 structured JSON 实测 |

完整性核验：

- `config.json`：`Qwen3_5ForConditionalGeneration`，text hidden size 4096，32 layers；
- `model.safetensors.index.json` 声明 4 个分片，总大小 19,306,216,416 bytes；
- 四个分片均能解析到本地 blob，缺失分片为 0；
- tokenizer JSON、tokenizer config、vocab、merges、chat template 均存在；
- Docker image 实际标签为 vLLM 0.25.0；旧容器退出码 0、`OOMKilled=false`；日志显示收到 SIGTERM 后正常 shutdown，因此不能断言具体由谁或为何停止；
- 历史运行多次返回 HTTP 200，表明它不是仅有目录名的残缺缓存。

除该模型外，搜索到的大权重文件只有 Qwen3 embedding 和 OvisOCR2；两者分别是 pooling embedding 与 OCR，均不作为生成式 LLM 证据。

## 为什么本轮不启动

旧容器在单卡上按 BF16 加载 16.8 GiB，并为 KV cache 使用额外显存。当前仅余约 13 GiB，直接启动无法在不停止 embedding/OCR 的前提下满足历史资源需求。任务明确要求不能影响现有服务，因此只将它记录为优先本地候选。后续若获得独立 GPU 时，应复用现有 vLLM 0.25.0 image 和本地 snapshot，在独立端口先做中文生成、Semantic Annotation schema、Fine Validation schema 三项最小验证。

项目默认 LLM 配置已改为 disabled + loopback local endpoint；不再把 DeepSeek 公网地址作为默认值。无本地服务时 provider 为 `None`，正式执行会明确报告缺少 LLM，不会回退到外部商业 API或 FakeLLM。

## Hybrid research

### RRF 原论文

Cormack、Clarke、Büttcher（SIGIR 2009）定义：

`RRF(d) = Σ 1 / (k + rank_i(d))`

它只使用各检索系统的 1-based rank，不要求 cosine 与 BM25 raw score 同量纲。论文在 pilot 中固定 `k=60`，后续 validation 未改变；实验认为该值近似最优但选择并不敏感。常数的作用是减弱单个系统异常高排名的支配，同时保留下位结果的非零影响。

### 近期论文

- IIMAS-RAG（SemEval 2026）让 sparse SPLADE 与 dense Voyage 各取 top-300，以等权 RRF、`k=60` 融合；其 top-100 reranker 在该数据上降低效果，因此最终提交排除 reranker。这同时支持“无权 RRF baseline”和“reranker 不是必需阶段”。
- Caraman（SemEval 2026）使用 BM25S 与 dense 各自召回，再以无需学习权重的 RRF (`k=60`) 融合。其后续 reranker 的最佳 candidate pool 随模型/数据变化，因此不能据此为本项目手工指定最优 K。

论文 PDF 和全文抽取保存在本目录的 `research/hybrid-retrieval/`。

### 成熟项目

- Elasticsearch 官方 RRF 对 child retrievers 等权，默认 `rank_constant=60`，先分别截取 `rank_window_size` 再融合。官方明确指出不同 relevance indicator 不需要相关或同尺度。
- Qdrant 官方提供 RRF、weighted RRF、DBSF。其选择指南明确：有 labeled eval set 时可调 weighted RRF；信任 raw-score distribution 时可试 DBSF；两者都没有时，RRF 是安全默认值。它还明确反对直接线性相加未归一化的 cosine 与 BM25。

因此本项目在没有人工 trace ground truth 时使用标准等权 RRF，不实现 weighted RRF、alpha score sum 或 DBSF。

## 当前正式数据流

```text
canonical SRS/SDS nodes
→ raw projection: heading path + original content
→ Dense Top-Kd (local qwen3-embedding-0.6b-1024)
  + BM25 Top-Kb (bm25s + jieba-py, full raw projection)
→ union
→ unweighted RRF(k=60)
→ per-query fused rank
→ RRF fine_top_k seeds
→ one-hop structural expansion
→ each final candidate assembles evidence independently
→ local Fine LLM (only when a verified local service is enabled)
→ direct-only TraceLink
```

正式路线不再请求或要求 reranker。原有 provider 文件与历史 nullable 数据列暂时保留，但新 run 的 `reranker_rank` / `reranker_score` 为 `NULL`，metrics 写入 `reranker_enabled=false`。`fine_top_k` 默认 20，只表示 baseline 的 RRF seed 数量；一跳提升为独立候选的结构节点也要单独 Fine Validation，因此实际 Fine 调用数可能大于 `fine_top_k`。

每个候选继续保存 `dense_rank/score`、`bm25_rank/score`、`rrf_score`、`fused_rank`、`retrieval_sources`。raw score 仅用于诊断，不参与 RRF。

## 本轮未测试

遵照用户要求，本轮未运行 pytest、Ruff、compileall、模型 smoke test、真实 Dense/BM25/RRF 或完整 TLR。代码只完成实现调整和静态依赖检查；运行验证留待用户后续明确指令。

## 资料链接

- RRF 原论文：https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- IIMAS-RAG 2026：https://aclanthology.org/2026.semeval-1.345/
- Caraman 2026：https://arxiv.org/abs/2605.12028
- Elasticsearch RRF：https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
- Qdrant Hybrid Queries：https://qdrant.tech/documentation/search/hybrid-queries/
