# Hybrid retrieval baseline evidence

本目录保存本次 TLR hybrid fusion 修复实际阅读的论文 PDF 与全文抽取文本。

## 采用的基线

正式 baseline 使用无权 Reciprocal Rank Fusion：

`RRF(d) = Σ 1 / (k + rank_i(d))`

默认 `k = 60`，输入通道严格为 raw dense 与 BM25。`semantic_synopsis` 仍可生成和持久化，但不参与 embedding、BM25 或 fusion。各检索器 raw score 只保留用于诊断，不参与融合。

## 文献与实现对应

- Cormack, Clarke, Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and Individual Rank Learning Methods* (SIGIR 2009)。原论文定义上述 rank-only 公式；`k=60` 在 pilot 中固定，后续 validation 未改；常数用于削弱个别系统异常高排名的支配，同时保留下位结果的非零贡献。它直接决定 `union_candidates()` 的公式、默认 `k` 和“不归一化不同 retriever raw score”的选择。
- IIMAS-RAG, *SemEval-2026 Task 8*。其正式检索把 SPLADE 与 Voyage dense top-300 通过等权 RRF (`k=60`) 融合，并报告 RRF 相对 linear fusion 的显著改善。论文评估过 reranker，但因本数据上降分而没有用于最终提交；因此这里只把它作为 2026 年 sparse+dense RRF 的复核证据，不把它误称为最终 RRF→reranker 实例。
- Caraman, *SemEval-2026 Task 8: Three-Stage Multi-Turn Retrieval with Query Rewriting, Hybrid Search, and Cross-Encoder Reranking*。其 Stage 2 使用 BM25S 与 BGE dense、无学习权重的 RRF (`k=60`)；论文还显示 reranker candidate pool 的效果依模型和数据而变化。它只用于复核 hybrid fusion，不构成本轮启用 reranker 的依据。
- Elasticsearch 官方 RRF：各 child retriever 等权，默认 `rank_constant=60`，各检索器先独立取 `rank_window_size`，再由协调节点融合；明确不要求不同 relevance score 可比。
- Qdrant 官方 Hybrid Queries：同时提供 RRF、weighted RRF、DBSF；其选择指南明确建议在没有 labeled eval set、也没有可靠 raw-score prior 时使用无权 RRF。Weighted RRF 的权重应由 train/validation evaluation set 决定，不能手调。

## 本项目差异

本项目查询和候选是 SRS/SDS canonical nodes，BM25 使用 `bm25s` + `jieba-py`，dense 复用服务器 Qwen3 embedding。结构扩展只对每个 query 的 RRF `fine_top_k` seed 做一跳 sibling/relation 扩展；扩展节点成为拥有自己 target node ID 的独立候选并单独 fine validate。supporting context 不参与 RRF，也不能替代候选自身的 direct evidence。当前正式路线不调用 reranker/cross-encoder。

官方实现资料：

- Elasticsearch RRF：https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
- Qdrant Hybrid Queries：https://qdrant.tech/documentation/search/hybrid-queries/
