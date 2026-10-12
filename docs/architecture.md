# 内容树与 TLR 主线

## 正式数据流

```text
PDF / DOCX
  → DoclingDocument
  → segmentation 规范化
  → document / section / group / paragraph / table / picture
  → ContentRelation（附属关系、显式引用）
  → 持久化唯一 canonical content tree
  → SemanticAnnotation（派生 role + synopsis）
  → raw/synopsis RetrievalProjection（引用 ContentNode）
  → Qwen3 dense + bm25s 中文检索
  → 无权 RRF(k=60) + 一跳结构扩展
  → configurable RRF Top-K（reranker 默认关闭）
  → 可回指节点的 EvidencePackage
  → LLM pair-level validation
  → pair-level TraceLink
```

非 Docling 的文本与代码也进入同一规范树。代码结构恢复不属于本轮文档 segmentation，当前将
经过语法校验的源码保存为一个 paragraph 叶节点，避免函数/类全文重叠复制。

## Canonical ContentNode

节点类型只有：

- `document`：根，不保存所有后代正文；
- `section`：标题和层级，不复制 section 正文；
- `group`：list、inline 等必要结构容器；
- `paragraph`：Docling 文本原文；
- `table`：完整 Docling TableItem/TableData；
- `picture`：完整 Docling PictureItem，不做图片理解。

叶节点只有 paragraph、table、picture。

每个节点分别保存：

- `parent_id + sibling_ordinal`：规范树及父内顺序；
- `reading_order`：文档全局稳定顺序；
- `node_type`：规范类型；
- `original_type`：Docling 原始 label；
- `docling_ref / docling_parent_ref / docling_children_refs / docling_depth`：原始结构；
- `heading_level`：Docling 标题级别；
- `content_layer`：body/furniture；
- `hierarchy_status / hierarchy_source`：原始层级是否被确定性修正；
- `provenance_json`：Docling 官方 provenance；
- `content_json`：table/picture 的官方结构，以及 document origin/page size；
- `text`：仅 paragraph 原文；
- `title`：仅 section/group 标题或名称。

不保存 heading path、previous/next ID 或 section 全文。它们都能从树和顺序动态计算。

## ContentRelation

关系独立保存，正文不因关系而复制：

- `caption_of`；
- `footnote_of`；
- `docling_reference_to`；
- `explicit_reference`。

每条关系记录 source、可空 target、resolved/unresolved、来源和原始引用文本。Docling 官方关系
优先；项目规则只处理原文明示的章节/图/表编号。

## TLR 派生层

TLR 不再查找 `semantic_unit`。SemanticAnnotation、RetrievalProjection、embedding、候选分数和
LLM 判断都按 run 保存为可删除重建的派生数据：

- paragraph raw projection 使用标题路径和原文；
- table 使用 Docling 官方 HTML serializer，保留 caption、表头和跨行跨列；
- picture 不标注、不 embedding、不作为独立 trace target；
- primary/uncertain 进入检索，context/exclude 保留供证据扩展；
- Dense 与 BM25 用标准无权 RRF 融合 rank；原始 score 仅诊断，不参与融合；
- 结构扩展限制为 sibling 和显式 ContentRelation 的一跳；
- 最终 evidence quote 必须逐字存在于对应 canonical ContentNode。

投影不写回 canonical tree，因此以后更换 embedding、reranker、LLM 或 token 限制不需要重建
文档结构。

## 已删除的旧行为

- ContentNode → `segment()` → `semantic_unit ContentNode` 的正文复制；
- 无 semantic unit 时整篇文档一个节点的 fallback；
- 30,000 字符固定窗口；
- canonical node 上的 summary、start/end 线性拼接偏移；
- document/section 保存完整后代正文；
- TLR 对 `semantic_unit` 的硬编码依赖。

技术依据及已知边界见 [segmentation-docling-research.md](segmentation-docling-research.md)。
