# Docling 后规范内容树：技术依据与边界

## 版本核对

- 重构前环境：`docling 2.127.0`、`docling-slim 2.127.0`、`docling-core 2.96.1`。
- 2026-09-27 查询 PyPI 时，Docling / docling-slim 最新可用版本为 `2.129.0`。
- 项目下限已提高到 `docling-slim >=2.129,<3.0`，本地验收环境也升级到 2.129.0。
- 2.128/2.129 的发布内容主要是格式支持、PDF backend 与 chart/S3 调整；规范树仍以
  docling-core 2.96 的 `DoclingDocument` 模型为准。

## 直接采用的官方数据模型

DoclingDocument 官方说明：
<https://docling-project.github.io/docling/concepts/docling_document/>

docling-core 官方模型源码：
<https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/document.py>

本项目直接使用下列官方事实，不重新发明等价字段：

- `body` 树的 children 顺序：正文阅读顺序；
- `parent` / `children` / `self_ref`：原始层级与 JSON Pointer；
- `content_layer`：body、furniture 等内容层；
- `SectionHeaderItem.level`：标题层级；
- `ProvenanceItem.page_no/bbox/charspan`：页、坐标框及项目内字符范围；
- `BoundingBox.l/t/r/b/coord_origin`：官方坐标框；
- `TableData.table_cells/num_rows/num_cols/orientation/grid`：表格真源；
- `TableCell` 的行列起止、row/col span、header 标志、bbox 与文本；
- `PictureItem.image`：Docling 已取得的原始图片对象；
- `captions` / `footnotes` / `references`：浮动对象的官方关系；
- `DocumentOrigin.mimetype/binary_hash/filename/uri`：源文件定位。

官方文档明确将 reading order 表达为 body 树中 children 的次序：
<https://github.com/docling-project/docling/blob/main/docs/concepts/docling_document.md>

## 标题与层级恢复

PDF 不再由项目实现字体阈值或版面启发式。项目直接启用 Docling 2.129 的
`HeadingHierarchyOptions(enabled=True)`。官方实现的优先级是：

1. PDF bookmark / outline；
2. 法规、章节编号；
3. 字号、字重、斜体、大小写等样式回退。

实现源码：
<https://github.com/docling-project/docling/blob/main/docling/models/stages/heading_hierarchy/heading_hierarchy_model.py>

相关近期问题：

- DOCX 自定义标题只设置 `w:outlineLvl` 时曾丢失标题，2026 年问题 #3959 / 修复 #3961：
  <https://github.com/docling-project/docling/issues/3959>
- DOCX 多级列表跨正文错误聚合并扰乱阅读顺序，问题 #3836 / 修复 #3838：
  <https://github.com/docling-project/docling/issues/3836>
- 2.129 仍存在列表跨表格恢复后顺序错误的已知边界 #4304：
  <https://github.com/docling-project/docling/issues/4304>
- PDF 标题层级长期限制及 bookmark 驱动改进讨论 #3676：
  <https://github.com/docling-project/docling/issues/3676>

规范化层只根据 Docling 已给出的 heading label/level 重建 section 栈。若规范父节点与原始
parent 不同，节点同时保存 `docling_parent_ref`、`hierarchy_status=corrected` 和
`hierarchy_source=postprocess:docling-heading-level`。不提升普通 text 为标题，不实现第二套标题模型。

## provenance 的现实边界

PDF pipeline 会在可用时提供 `page_no + bbox + charspan`。这些结构按 Docling JSON 原样保存，
page size 也保存在 document 根节点中，供 UI 坐标还原。

DOCX backend 对本次验收文件输出 `pages={}` 且所有 item 的 `prov=[]`。这表示 Docling 没有
提供版面页码，不应根据 Word 分页或线性文本 offset 伪造 provenance。本项目对 DOCX 使用
`DocumentOrigin + docling_ref + parent/children + reading_order` 返回源文件中的逻辑位置，并明确
保留空 provenance。

## caption、footnote 与显式引用

Docling 的 caption / footnote reading-order 关联来自官方 reading-order stage，结果写入浮动
对象的 `captions` 和 `footnotes`：
<https://github.com/docling-project/docling/blob/main/docling/models/stages/reading_order/readingorder_model.py>

Docling 2.129 尚未普遍恢复 PDF/DOCX 的所有内部交叉引用。2026 年 tagged-PDF 提案 #4146
仍把 intra-document target 标为 pipeline gap：
<https://github.com/docling-project/docling/issues/4146>

因此项目只补充小规模、可替换的确定性规则：

- 标题编号精确匹配“第 X 节/章”；
- 图、表编号只识别原文明示的“图 X / 表 X”；
- 编号 caption 是对象锚点；
- 没有 caption 时，仅当段落只提到一个编号、带“所示/如下”等显式措辞、且同 section
  邻域只有一个对应对象时，建立编号锚点；
- 不能唯一解析时创建 `status=unresolved` 关系，不猜目标；
- 不使用 LLM，不恢复隐式语义引用。

## 对比过但未引入的实现

- Unstructured 提供 element `parent_id` 和 page/coordinate metadata，但其公开讨论也显示扁平
  element 序列需调用方另存顺序；引入它会形成第二条解析真源：
  <https://github.com/Unstructured-IO/unstructured/discussions/3492>
- GROBID 擅长学术 PDF 的 TEI、引文、图表结构，不覆盖本项目的通用 DOCX/PDF 工程文档输入，
  因而未作为依赖：<https://grobid.readthedocs.io/>
- Docling 的 HierarchicalChunker / HybridChunker 是检索切块器。其 chunk 可以引用原始 doc items，
  但不应反向改变 canonical tree；本项目将来可在 retrieval projection 层复用：
  <https://docling-project.github.io/docling/concepts/chunking/>

Docling 论文与技术报告：

- <https://arxiv.org/abs/2501.17887>
- <https://arxiv.org/abs/2408.09869>

## 项目自定义部分

仅有三类薄适配逻辑：

1. Docling label 到 paragraph/table/picture/section/group 的规范类型映射；
2. 用 Docling heading level 修复少数原始 parent 异常，并保留修正来源；
3. 上述显式编号引用规则。

没有实现 OCR、图片理解、LLM 摘要、内容性质分类、固定窗口切分或大型标题启发式系统。
