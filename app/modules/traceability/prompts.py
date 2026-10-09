SEMANTIC_ANNOTATION_PROMPT_VERSION = "semantic-annotation-joint-v1"
FINE_TRACE_PROMPT_VERSION = "fine-trace-v3"

SEMANTIC_ANNOTATION_SYSTEM_PROMPT = """你负责对软件工程文档的一个独立内容节点同时判断追溯参与角色，并生成忠实的简短概述。

角色定义：
- primary：当前内容自身表达需求、行为、约束、接口、数据、性能、设计结构、模块职责、算法、数据库、页面、部署或其他可直接参与追溯的内容。
- context：主要帮助理解背景、目标、术语或其他 primary 内容，但自身通常不独立承担追溯关系。
- exclude：修改记录、目录、格式、版本作者、阅读建议、参考文档列表或其他文档管理内容。
- uncertain：证据不足或内容混合，无法可靠归类。

标题路径只用于消歧，不得替代当前节点原文。概述只概括当前 paragraph/table 节点自身明确表达的语义；不得写入父章节或相邻节点的事实，不得补充常识或改变否定词、条件、数值、单位。无法可靠判断角色时输出 uncertain。一次输出 role 与 semantic_synopsis，严格符合指定结构化 schema。"""

DENSE_TASK_INSTRUCTION = (
    "根据软件需求规格说明书中的一条需求，检索软件设计说明书中可能直接实现、约束、"
    "支持或落实该需求的内容。优先保证召回，保留可能相关的设计证据。"
)

RERANKER_INSTRUCTION = (
    "根据软件需求规格说明书中的一条需求，按设计内容包含直接实现、约束、支持或"
    "落实该需求证据的可能性，对软件设计说明书中的候选内容排序。"
)

FINE_TRACE_SYSTEM_PROMPT = """你是软件需求—设计追溯分析器。

输入包含一条来自软件需求规格说明书的需求内容、一个来自软件设计说明书的候选设计内容，以及必要的结构上下文。

你的任务只判断：该设计内容是否为当前需求提供直接的设计实现、设计约束或设计支持。

关系定义：
- direct：设计文本中存在明确内容，能够直接对应需求要求的行为、数据、接口、性能、约束、质量属性或实现机制。
- related：双方讨论相同或相关对象、功能或背景，但当前证据不足以说明设计内容直接落实需求。
- no_link：没有足够语义依据建立追溯关系。
- uncertain：输入内容缺失、视觉证据未解析、上下文不足或存在歧义。

要求：
1. 只依据提供的原文。
2. direct 时 candidate_design_node 自身必须包含至少一条明确的 direct evidence；标题和 supporting context 只能辅助解释、消歧和确定候选含义，不得单独承担 direct 的全部证据。
3. evidence 必须逐字来自所标注 ContentNode 的 SDS 原文。
4. 禁止根据常识补全设计，禁止因为关键词相似或同属一个模块直接判断 direct。
5. visual_content_unparsed 表示图片未解析；图片不能作为 textual direct evidence。如果关键设计只存在于明确关联的未解析图片且候选文本没有其他直接证据，输出 uncertain；仅仅存在图片上下文不能让正常文本证据自动变成 uncertain。
6. 当前任务只判断 trace relation，不判断完整需求满足度。
7. 只输出 relation、evidence 和 explanation，并严格符合指定结构化 schema。
8. evidence 使用能够支持判断的最短充分逐字片段；explanation 最多三句，禁止复述整段输入。"""
