# 需求层级、匹配与实现判断

## 模块边界与依赖

| 模块 | 本次变化和职责 | 依赖 |
| --- | --- | --- |
| requirements（新增） | 保留原文地细分需求；生成章节摘要、高层功能类别和子类别；保存分析树、未分配项、原始模型输出和源指纹 | 已有 TLR 数据集/文档切分、LLMProvider、租户模型配置、SQLAlchemy/Pydantic |
| tlr（扩展） | 把需求叶节点转换为可追溯元素；按语义类别筛选代码候选，再复用原检索和成对分类；保存实际比较统计 | requirements 分析快照、现有 embedding、Python/LISSA 检索、PairClassifier |
| consistency（改造为 v2） | 汇集多代码证据；逐段记录实现部分；校验引文并输出四种可共存结果 | TLR 候选和元素、可选需求树、现有代码结构提取、LLMProvider |
| model_config / API / 数据库（扩展） | requirements_analysis 模型任务、需求分析路由、requirement_analyses 表 | 现有服务基础设施；迁移 20260913_0008_requirements.py |
| scripts / docs（扩展） | 一体化运行入口、v2 报告、隔离冒烟样例和使用说明 | 上述模块及项目已有环境 |

需求理解适合独立模块：它可复用于需求质量、设计一致性或测试覆盖，不应绑死在 TLR。TLR 负责建立关系，一致性模块负责解释关系及判断实际行为。requirements 暂时复用 TLR 的快照与切分基础设施，并非完全独立存储平台。

没有新增第三方库或下载项目作为运行依赖；复用现有 embedding、LLM、数据库与源码解析能力。研究资料是设计依据，不是运行时必需。原 TLR 默认行为保持不变，只有设置 requirements_run_id 才启用新路径；不删除或重建协作者的表和数据。

## 基线及本项目差异

[Spec2Code 原论文](https://www.jianjunchen.com/p/rfc2code.ase25.pdf) 从协议中抽取规范要求，结合周边段落和引用拆出触发—动作约束，借助章节摘要建立高层类别/子类别，将规范要求分配至类别，再对函数进行自上而下匹配。多个局部函数联合实现时还检查调用关系并进行二次判断。

本次采用它的“章节摘要→两级功能类别→细粒度要求→代码候选”组织思路。不同之处：
- 通用需求不以 MUST/SHOULD 等协议关键词作为入选条件，不强求原子化。
- 向下切分和向上分类是两种关系。一个原文单元可以属于多个类别，不能把语义类别冒充原文父章节。
- 代码路由采用现有 embedding 相似度，非论文同款逐层 LLM 分类；保留回退与实际成本计数。
- 没有复现论文的完整调用图联合证明；上下文不足时保留判断出错，不能用多个局部命中代替整体实现。

[EARS](https://alistairmavin.com/ears/) 用于提醒保留触发条件、状态、响应与可选约束，不强制把自然语言改写成 EARS 后当作原需求。[需求重叠聚类论文](https://doi.org/10.1017/dsj.2025.10019) 提供需求可能跨功能群的参考，本次采用多重归属，没有实现其图聚类算法。

原论文抽取、网页原文和下载清单在 artifacts/requirements/sources/；AI 参考文档仅作为检索线索。机器可读字段契约另保存在 artifacts/requirements/contracts.json 和 artifacts/consistency/contracts.json。

## 层级构建

~~~text
原始需求工件
  └─原文章节/有标记的长度窗口
      └─需求子单元（可递归）
          └─更小单元，或不能细分的叶节点

高层功能类别
  └─功能子类别
      └─章节归属 → 对应的需求叶节点（允许多重归属）
~~~

原文树保存字符起止位置及父子关系。细分引用必须为连续、唯一定位、不重叠且小于父段的原文；共享条件必须引用原文，子单元继承条件。没有被子引用覆盖的非空内容成为 residual 叶节点，防止遗漏约束。改写 normalized 仅为建议，不是新增验收条件；句子排列也不自动视为执行顺序。

不能拆分保留 indivisible；歧义保留 ambiguous；模型错误、预算耗尽和残余内容保留可见叶节点及原因。原文不会因模型只提取部分内容而消失。过长叶节点可为检索建立窗口，但窗口不是新造的语义子需求。

向上分类以章节摘要建立 category/subcategory，记录 section_ids。类别不可靠、没有合适类别、模型调用失败或摘要超过预算时，相关章节留在 unassigned_section_ids，进入全局匹配。构建完成只表示分析过程完成，不代表每条需求都成功细分或归类。

主要限制通过 BuildInput 设置：max_depth 默认 2（上限 3）、section_chars 默认 10000、max_units 默认 500、cluster_chars 默认 40000。budget 回退保留原文，不能把提高预算等同于提高正确率。

## 每一步调用哪些模块

| 步骤 | 调用模块 | 输入与产物 |
| --- | --- | --- |
| 1. 导入快照 | tlr 的现有导入服务 | 需求及实际源码正文→数据集、工件、原有文档层级 |
| 2. 向下细分 | requirements.service → processing.analyze_artifact → 现有模型提供器 | 章节及原文→父子节点、共享条件、原文偏移、不可细分/错误原因 |
| 3. 向上归类 | requirements.processing.cluster | 章节摘要→高层功能类别/子类别、多重归属、未分配章节 |
| 4. 准备匹配元素 | tlr.service → hierarchical.requirement_elements；代码侧原预处理器 | 叶节点/检索窗口、代码函数等元素，附祖先及相邻上下文 |
| 5. 分层候选检索 | hierarchical.HierarchicalRetriever → embedding → 原检索器 | 代码先路由至高层类别、再至子类别；候选池内检索 |
| 6. 关系分类 | 原 PairClassifier | 原文元素+需求上下文+代码→TLR 关联，仍校验原文引用 |
| 7. 证据准备 | consistency.service → structure | 所有相关代码及需求树→prepared 证据快照 |
| 8. 实现判断/补读 | consistency.judge + service | 多工件上下文→按代码/需求部分组织的 implementations |
| 9. 校验和报告 | judge.conclude → service.view → 报告脚本 | 四类非互斥 outcomes、引文位置、映射、错误原因；复核独立追加 |

匹配阶段的父级上下文存在长度上限并标记截断；真正的判断回到需求快照，不把检索摘要当事实。多代码联合实现时同时考虑共享条件、调用连接、顺序和状态；具体边界见 [一致性说明](consistency.md)。

## 回退与比较开销

RunOptions 新增：
- requirements_run_id：完成的需求分析 ID，同数据集且原文指纹一致。
- hierarchy_top_categories=1、hierarchy_top_subcategories=2：每层保留类别数。
- hierarchy_min_similarity=0.15：低于阈值时回退。
- hierarchy_probe_targets=1：对类别外代码进行确定性补充探测，可设置为 0。

无类别、类别向量调用失败或无法使用、路由分数太低、候选池为空时有全局回退；未归类、歧义、残余或分析失败的源单元保守匹配全部代码。按相同候选池合并检索调用，减少重复调用原检索器。仍保留项目现有最大比较数量限制。

manifest.hierarchical_retrieval 记录 full_pairs、evaluated_pairs、pair_reduction、类别比较成本、路由及 scope_complete。它比较的是同一组叶节点/代码元素的全量与路由后检索配对，不是总运行时间。建树的 LLM 调用和类别 embedding 自身也有成本，小项目未必更快。裁剪候选存在召回损失；少量类别外探测不是完整性证明。

## 运行入口

先通过既有 TLR 导入接口导入带正文的需求和代码；reference_only 只是引用目录，不能当源码。

~~~powershell
.venv/Scripts/python.exe -m scripts.init_consistency
.venv/Scripts/python.exe -m scripts.run_requirement_workflow --tenant-id local --project-id YOUR_PROJECT --dataset-id YOUR_DATASET --requirement-id YOUR_REQUIREMENT --match --consistency
~~~

requirement-id 可重复；target-id 可指定代码工件，不提供则选择数据集中的实际代码。省略 match/consistency 只建需求树。输出目录 outputs/requirement-workflow/<requirements_run_id>/ 包含 requirements.json、TLR manifest 和一致性 JSON/Markdown，以及可恢复使用的 run ID。

API：POST /api/v1/requirements/analyses 构建；GET /api/v1/requirements/analyses/{identifier} 查看。构建当前为同步请求，较大文档调用时间可能较长。模型绑定优先级 requirements_analysis → architecture_extraction → tlr_classification → 默认配置。

## 本次必要验证

- 新路由及 OpenAPI 可加载。
- 隔离端到端样例覆盖细分、不可细分、歧义、未归类回退、分层检索、实现记录及冲突/判断出错共存。最新材料：artifacts/requirements/smoke/9f9b89ac-f58a-4192-8868-2360ae89e47e/。
- 该人工样例的代码检索比较为 5/8；仅证明路由生效，不是速度或召回率基准。
- 实际已配置模型的需求层级结果在 artifacts/requirements/live-hierarchy.json；实际 v2 一致性结果在 artifacts/consistency/smoke/40bdd908-9a4c-41be-bc4c-fa9849fa6dc5/。
- 没有运行全量测试、修改已有数据集或执行 Git 命令。真实小样例不能代替大规模业务效果评估。
