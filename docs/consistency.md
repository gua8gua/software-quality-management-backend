# 需求—代码一致性判断（v2）

本模块接收 TLR 关联和可选的需求层级，按相关代码记录它实现了需求的哪些部分。详细的模块划分、层级构建与匹配见 [需求层级流程](requirements-hierarchy.md)。

为独立分析详情及单元导航，新建快照保存 `requirement_structure`（本次源工件的完整节点树）和 `tlr_elements`（本次范围内匹配片段的 ID、角色、原工件 ID、正文、偏移及处理元数据）。`links` 附带源单元/片段标识和目标原文偏移，支持上下层定位。功能分类继续使用 `packets.semantic_categories` 与节点 `categories`；目标语法结构使用既有 `code_structure`，文档章节使用 `document_structure`。这些是既有 LiSSA/TLR、需求拆分及语法结构的展示适配，未引入新的质量判定规则。

允许复用部分完成的 TLR（`status=completed, stage=completed_with_errors`），沿用现有 TLR 阶段和已确认关联，不将待判定候选转换为负向关联。分析快照保存 `tlr_stage`、`tlr_partial` 和 `tlr_pending_candidates`；代码分析将部分完成标为匹配范围不完整，沿用既有缺失功能判定约束。`python -m scripts.smoke_consistency --partial` 使用隔离数据库和模拟模型验证此流程。

“整体判断”指单份源制品所表达需求的完整满足情况：代码贡献记录和叶节点结果保留，另检查多个函数的连接、顺序、状态和条件是否共同满足该需求。如果源制品是一整份文档，结果外层仍按文档组织，细分需求记录在 `parts.unit_id`；并非所有叶节点都分别进行独立模型调用。超过正文预算时按叶节点调用，并将跨段协同实现保留为未确认。

“按需补读”由模型在分析中返回 `requested_artifact_ids` 触发，不依赖用户另外提问。后端先提供同一快照的可读资料目录（ID、类型、路径、标题、每份最多 30 个代码符号，目录最多 300 项），再按有效 ID 读取整份正文并重新判断。默认允许补读 1 轮，接口最多 2 轮，每轮最多请求 10 份资料，证据正文默认预算 60000 字符。它目前只支持目录内按 ID 补读，尚无按语义搜索目录或按函数范围读取的工具；目录省略或读取超预算会记录限制。所选目标以外的资料只能补充解释上下文，不能替代目标证明实现。

## 结果口径

结果字段为 outcomes 数组；只有以下四种值，允许共存，不再返回 findings 或单一互斥 verdict。

| 值 | 含义 |
| --- | --- |
| implemented（实现） | 引用的代码实现了明确说明的需求部分，不自动表示整个需求已经实现 |
| missing_functionality（缺失功能） | 明确要求的行为未实现，且已说明缺失行为和足够完整的检查范围 |
| conflict（冲突） | 有原文和实际代码支持的行为矛盾 |
| judgement_error（判断出错） | 某部分无法可靠判定，包括证据不足、需求歧义、模型格式错误、无效引文或上下文超限；不等同于软件运行错误 |

需求级 outcomes 汇总局部结果。例如同一大需求可以同时有实现、冲突和判断出错。不能用“TLR 没匹配到”证明缺失功能；匹配范围不完整、证据有省略等情况下，缺失判断降为判断出错。whole_requirement_implemented 另行表示是否足以支持整个需求已实现；不能通过命中数或局部百分比直接计算。

implementations 按代码组织，每条记录包含 citations 和 parts。每个 part 包括 unit_id、requirement_quote、implemented_behavior、outcomes、reason、missing_behavior、scope_complete。多个函数协作时可以分别说明贡献，也可在同一记录引用多段代码说明联合关系。

~~~json
{
  "outcomes": ["implemented", "conflict", "judgement_error"],
  "implementations": [
    {
      "citations": [{"evidence_id": "代码证据ID", "quote": "实际代码原文", "start": 0}],
      "parts": [{
        "unit_id": "需求叶节点ID",
        "requirement_quote": "需求原文片段",
        "implemented_behavior": "该代码实际完成的行为",
        "outcomes": ["conflict"],
        "reason": "实际行为与该原文要求之间的具体差异",
        "missing_behavior": "",
        "scope_complete": false
      }]
    }
  ]
}
~~~

这是字段示意，具体引文必须来自输入快照。最终结果补充可校验的位置、源文件、行号、原文偏移、摘要及 grounding_errors。implementation_mappings 提供代码与需求部分的多对多映射；语法符号定位选择包含完整引文的最小符号。

## 执行过程与上下文

1. create 冻结原始需求、已有文档层级、需求分析树、TLR 关联、代码结构和多工件证据，状态 prepared 表示“证据已准备，尚未调用判断模型”。
2. execute 把证据包交给模型。工件可以是代码、设计等，但认定代码实现必须有代码引文。模型只能申请同一快照 available_artifacts 中的 ID；后端检查范围并有限补读，默认一轮、最多两轮。它不是可以自行浏览磁盘或互联网的收集代理。
3. 结合需求原文、父级条件、叶节点、语义类别、相邻上下文及相关代码判断各个实现部分。类别及改写只是匹配建议，不能替代原始需求。
4. 后端逐字校验引用。唯一匹配可修复模型给错的偏移；伪造引用或错误单元引用使受影响记录变为判断出错。某条记录或某个 part 格式错误时尽量保留其他有效记录。
5. 返回非互斥结果、实现记录、判定原因和未评估代码。reviews 可附加人工、已有测试或运行轨迹证据，不覆盖原分析。

omitted 是因证据预算等原因未装入证据包的内容清单，绝不是“缺失功能”清单。analysis_errors / grounding_errors 是后端判定规则未通过的原因，例如代码引文不准确、需求单元无有效分析或缺失功能的检查范围未成立。

对于超过单次预算的需求，有层级时按叶节点及附近原文分析，带入共享条件和关联代码。保留所有局部结果；当前尚未实现可靠的跨批次全局执行验证，因此增加判断出错并保持 whole_requirement_implemented=false。不可细分且自身超过预算的单元明确记录上下文不足，不能截取后冒充完整判断。

当前代码结构是语法层级，不是完整的跨语言调用图或运行轨迹。普通预算内可由模型结合多处实际代码分析连接关系，但不能保证动态分派、跨服务调用、事务和并发条件都已覆盖。

## 使用

后端根目录执行；环境使用已有依赖及模型配置。SQLite 初始化脚本只补建 requirements/consistency 表，不改写已有业务数据。部署使用项目既有迁移流程，新增迁移为 0008（需求分析表，依赖 0007）。

~~~powershell
.venv/Scripts/python.exe -m scripts.init_consistency
.venv/Scripts/python.exe -m scripts.run_consistency --tenant-id local --project-id YOUR_PROJECT --tlr-run-id YOUR_TLR_RUN --requirements-run-id YOUR_REQUIREMENTS_RUN --prepare-only
.venv/Scripts/python.exe -m scripts.run_consistency --tenant-id local --project-id YOUR_PROJECT --run-id PREPARED_RUN
~~~

requirements-run-id 可省略：优先使用 TLR 已冻结的需求分析树；没有树仍兼容原始需求判断。显式提供时要求同一数据集及原文指纹一致。requirement-id 可重复，每次最多 5000 条，与快照和 TLR 容量一致，支持整层文档提交；context-id 可补充同一快照工件。输出在 outputs/consistency/<run_id>/，保留 JSON 和 Markdown。失败调用可按原 run ID 重试；已有成功分析不自动重做。

API 均沿用 tenant_id / project_id 作用域：
- GET /api/v1/consistency/runs：项目分析历史，offset/limit 分页，返回摘要及关联 TLR/数据集 ID，不加载全部报告正文。
- POST /api/v1/consistency/runs：准备。
- GET /api/v1/consistency/runs/{run_id}：查看。
- POST /api/v1/consistency/runs/{run_id}/execute：判断。
- POST /api/v1/consistency/runs/{run_id}/reviews：追加复核。

模型复用 LLMProvider.chat_json，绑定顺序为 consistency_judgement → tlr_classification → 既有默认配置。未新增模型 SDK。

前端项目页已提供“一致性分析”入口：每次选择当前快照已完整完成的需求→代码 TLR，或从零建立需求层级并重新运行 TLR 后判断。启动后自动执行，历史记录可查看结果、继续待判断任务、重试失败判断及导出 JSON。前置流程当前由页面串联，应保持页面打开；已经保存的 TLR 和一致性记录可从历史重新进入。前端操作说明见 software-quality-agent/frontend/README.md。

历史 v1 数据不原地迁移或删除；读取时由 compatibility.py 转换为 v2 视图，标记 legacy_projection。历史支持/冲突只在相应证据允许时投影，旧的不确定判断不会被升级成缺失功能。旧消费者需要适配 outcomes 和 implementations。

## 验证范围

已运行隔离的层级→TLR→一致性流程，并验证冲突与判断出错共存、异常局部输出不抹掉有效冲突、伪造引文被拒绝。也使用已配置的真实模型分别运行小规模需求层级和 v2 一致性调用。材料在 artifacts/requirements/ 和 artifacts/consistency/。这验证可运行性和数据链路，不是语义准确率、召回率或完整需求实现证明。
