# TLR 分层、制品粒度与数据结构（2026-09-06）

## 实际排障结论

原环境没有运行中的 8000 后端，脚本又默认继承 HTTP 代理，导致本机调用出现 502。启动后端并绕过本机代理后，真实运行在 embedding 阶段失败：MODEL_API_KEY 为空，同时 LLM_API_KEY 也为空。新版在执行前检查，将缺失项保存到 run.error 和 manifest.failure；CLI 导出 failure.json 并返回非零退出码。没有用测试模型冒充真实模型完成检测。

真实排障证据位于 artifacts/tlr/diagnostic/failure-before.json、artifacts/tlr/diagnostic/after/ 和 artifacts/tlr/safa/live-run/。独立测试模型通过完整的预处理、向量化、检索、判定、聚合和持久化，见 artifacts/tlr/safa/test-model-run/；这只验证软件流程，不代表 TLR 精度。

## 论文与可复用实现基线

- LiSSA 论文第 III.A 节（本地 artifacts/lissa/paper.txt）定义 None、Sentence、Chunk(N)、Method、Models Feature Extraction。原始 Java 实现固定于 a8e652f29bbdcc4bdcd3d98cd85aa4a37cb65480，保存在 vendor/lissa/。
- 原始 CodeMethodPreprocessor 使用 Tree-sitter Java，建立文件—类—方法元素关系。本项目复用 Tree-sitter Java 语法解析，按声明边界保留前置注释、头部和间隙文本；字符偏移和所属制品保存在每个单元中。这不是原实现的逐字节等价复现。
- 原论文对架构模型按元模型提取组件、接口、操作及依赖，再模板化为文本；并非 LLM 提取架构文档。当前提供明确的组件 JSON 适配器；完整 EMF/UML 模型解析尚未接入。
- LLM 架构文档抽取是本项目的显式扩展，默认不开启。抽取名称、类型和逐字引文，引文不在原文中则失败。检索文本使用有来源的引文，抽取的结构与提示摘要保存在 processing。
- [Tree-sitter Python](https://tree-sitter.github.io/py-tree-sitter/) 是 Java AST 解析接口基线；Python 代码使用标准库 ast。

## 数据集更换与真实边界

新增主要展示数据为 [SAREC-Lab/SAFA-Artifacts](https://github.com/SAREC-Lab/SAFA-Artifacts)，来自 ICSE 2019 “Leveraging Artifact Trees to Evolve and Reuse Safety Cases”。固定提交 ac201a4a7364cf91110c33a9f5b41df9ffbfe709，MIT 许可。原始 JSON、论文 PDF、Graphviz 关系图和 LICENSE 均保留在相邻 Agent 仓库 datasets/SAFA-Artifacts。

V1 转换后 360 个记录、355 条原始关系，保留 13 种 original_type：Hazard、Requirement、SafetyRequirement、ProcessRequirement、DesignDefinition、Code、Package、AcceptanceTest、Simulation、Context、EnvironmentalAssumption、FormalReview、SafetyAnalysis。归一化为 9 种业务类型：风险 16、需求 25、自然语言 54、测试用例 8、设计 32、包 68、代码引用 155、评审 1、其它生命周期文档 1。

223 个包/代码节点只有引用，不能当作源码正文。默认 TLR 仅使用剩余 137 个有正文的记录；部分原始描述本身很短或为占位文本，转换器保留原状，不生成假内容。该数据集比 SMOS 覆盖类型更多，但不是完整源码交付包，且不包含所有生命周期阶段。SMOS 仍保留，供完整源码分割与需求—代码验证。不会将两个软件的数据混为一个项目。

运行 python -m scripts.prepare_safa 可重复生成 artifacts/tlr/safa/dataset.json、manifest.json 和 reference-links.json。原始树中同一个 ID 若正文不同，会以内容摘要区分；原 ID 始终保留。Reference-links 只用于来源核对或独立评测，绝不注入 embedding/判定输入，也不作为新运行的最终结果。

## 分割策略

| 输入 | 自动策略 | 可显式比较 |
|---|---|---|
| Java/Python 源码、测试代码 | Method；无法解析或超长时 Chunk，记录降级 | None / Chunk / Method / Class |
| 其它语言源码 | Chunk | None / Chunk |
| 需求、风险、其它自然语言文档 | Sentence | None / Sentence / Chunk / Sections |
| 设计和其它生命周期文档 | Sections，按标题或段落 | None / Sections / Chunk / LLM Structure |
| 测试用例 | 完整制品，保留前提、步骤、预期的关联 | 可显式选择其它策略 |
| 架构模型 | Model Features（组件 JSON） | None / Model Features |

源码语言按 options.code_language、制品 structure.language、文件扩展名的顺序解析。SMOS 的 .txt 实际为 Java，prepare_smos 已保存这一事实。单元超长不静默截断；自动代码分割的 Chunk 降级保存 requested_strategy、fallback_from、fallback_reason。手工选 Method 时语法错误会失败，保证实验策略一致。

组件 JSON 输入示例：

~~~json
{"components":[{"name":"Login","operations":["login"],"dependencies":["UserStore"]}],"interfaces":[{"name":"AuthAPI","operations":["authenticate"]}]}
~~~

模型特征文本属于派生文本，processing.derived=true，偏移指向整个模型；UI 会区分它和逐字原文。完整 EMF/UML 模型需要另写适配器，不能将任意 XML 当成上述 JSON。

## 多选层间矩阵

默认顺序为：背景/风险/自然语言 → 需求 → 架构/接口 → 详细设计 → 代码 → 测试 → 评审/其它生命周期 → 发布/运维。这是本项目的工作流约定，不是论文规定的唯一顺序；单份数据可通过 structure.layer 指定受支持的层。

默认比较相邻非空层，跳过空层和 reference_only/package 记录。行表示源，列表示目标，可多选非相邻比较及反向比较；两方向不镜像，因为 Top-k 检索有方向性。同层对角线不选，若需同类制品相互比较可使用手工源/目标模式。

每一格创建一个 TlrRun，config.plan_id 关联批次，config.layer_pair 记录方向，配置、输入快照和结果独立保存。POST /api/v1/tlr/plans 在事务中创建整批，GET /api/v1/tlr/datasets/{id}/layers 返回计数和默认对。请求 pairs=null 使用默认；显式空列表、重复对或空层都会拒绝。当前网页顺序执行，关闭页面会中断尚未发出的任务；已创建 pending 记录可从历史逐个继续，没有后台任务队列。

~~~json
{"tenant_id":"local","project_id":"dronology-safa","dataset_id":"实际快照ID","pairs":[{"source":"requirements","target":"design"},{"source":"requirements","target":"verification"}],"options":{"source_preprocessor":"auto","target_preprocessor":"auto","kind_preprocessors":{"code":"method","test_case":"artifact"},"code_language":"auto","top_k":3}}
~~~

## 结构字段与隔离

新增 tlr_artifacts.structure JSON，包含 original_id、original_type、title、source_file/path、parent_ids、relations、tree_paths、content_status、source_record、repository、commit、language 等可扩展元数据。parent_ids/relations.target_id 引用同快照的 external_id，UI 按当前快照解析；同节点多父关系保留。

SAFA 父子边是原始数据提供的追踪树关系，并非文件夹包含关系。关系类型保存为 dataset_trace，原边类型另存 source_relation_type。它与 tlr_links 模型输出分开。SMOS 只提供集合和文件路径，因此保留 path/collection，不凭相似度伪造父子关系。

新增 tlr_elements.processing JSON，保存实际 strategy/version、偏移单位、派生文本标记、结构特征、模型和提示摘要、自动降级情况。成功处理的制品单元会逐份提交；后续失败仍可查询。运行整体失败不能作为最终评测。

## 升级、复现与协作

本地 SQLite 已执行追加列升级并备份到 artifacts/database-backups。其他本地 SQLite 环境在后端目录执行 python -m scripts.upgrade_local_tlr；PostgreSQL 执行 alembic upgrade head（20260906_0004）。不要重建数据库来升级。

~~~powershell
# 当前后端的真实流程，缺模型配置时导出明确失败原因
.venv/Scripts/python -m scripts.run_tlr_plan --dataset artifacts/tlr/safa/dataset.json --execute --output artifacts/tlr/safa/live-run
# 只创建可检查的待执行计划，省略 --execute
.venv/Scripts/python -m scripts.run_tlr_plan --dataset artifacts/tlr/safa/dataset.json
# 对真实源码进行三种粒度和自动策略审计，不调用模型
.venv/Scripts/python -m scripts.audit_tlr_preprocessing
~~~

scripts/run_tlr_plan 每次导入新快照、建立独立计划，不覆盖已有结果。CLI 默认绕过系统 HTTP 代理访问本机。候选、单元、链接、评测、失败响应全部写入指定输出目录。

协作边界：preprocessing.py 负责不同类型处理；planning.py 负责层映射和计划；diagnostics.py 负责安全错误信息；prepare_safa.py 负责带来源的转换；前端 LayerAnalysis.tsx 负责矩阵输入与汇总，Structure.tsx 负责原始关系树与详情。API/UI 均不自动把已有数据关系当作模型证据。
