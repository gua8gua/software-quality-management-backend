# Agent Evidence Evaluation：资料阅读与实现约束

本目录保留本轮实际阅读的论文原文。实现不是把论文框架原样拼装，而是抽取与
SRS→SDS evidence evaluation 直接有关、且能由当前 canonical tree / TLR 数据验证的原则。

## 资料与采用点

### Agent-as-a-Judge: Evaluate Agents with Agents（ICML 2025）

- 原文：`agent-as-a-judge.pdf`；[ICML 页面](https://icml.cc/virtual/2025/poster/45485)，[arXiv](https://arxiv.org/abs/2410.10934)。
- 阅读重点：正文第 4 节、附录 A、K、L。
- 论文以 hierarchical requirements 和 ask/graph/read/locate/retrieve 等模块对过程级证据做评价；消融还显示不受约束的 search、planning、memory 会引入噪声或偏差。
- 本项目采用：Requirement→Claim 的分层评价、读取/定位/检索能力分离、过程级 Tool trail。
- 本项目没有照搬：自由规划、长期记忆、额外 graph；主顺序由代码控制。

### FIRE: Fact-checking with Iterative Retrieval and Verification（Findings of NAACL 2025）

- 原文：`fire-iterative-retrieval-verification.pdf`；[ACL Anthology](https://aclanthology.org/2025.findings-naacl.158/)。
- 阅读重点：第 3 节框架、终止与过度自信防护、最终 verification。
- 论文将 verification 和下一轮 query 生成统一为迭代过程，并以最大检索步数、连续相似 query/result 等条件停止，最后基于累计 evidence 做 final verification。
- 本项目采用：Judge 可以返回 claim-specific Evidence Request；Investigator 再调查；配置化最大循环、检索轮次和重复 query/node 限制。
- 本项目没有照搬：Web search 与模型参数知识。所有检索范围限定在当前 TLR 的 SDS target scope。

### Search-o1: Agentic Search-Enhanced Large Reasoning Models（EMNLP 2025）

- 原文：`search-o1.pdf`；[ACL Anthology](https://aclanthology.org/2025.emnlp-main.276/)，[代码](https://github.com/RUC-NLPIR/Search-o1)。
- 阅读重点：第 3.2–3.3 节、Reason-in-Documents、Algorithm 1。
- 论文允许知识不足时动态检索，并用独立 Reason-in-Documents 先压缩/筛读检索文档，避免把冗长结果直接注入主推理。
- 本项目采用：BM25/Dense/preview/synopsis 只进入 navigation result；Investigator 必须再调用 canonical node reader，只有筛选后的 exact quote 进入 Evidence Workspace。
- 本项目没有照搬：长 CoT 注入与外部搜索；不保存或展示模型隐藏推理。

### Can External Validation Tools Improve Annotation Quality for LLM-as-a-Judge?（ACL 2025）

- 原文：`external-validation-judge.pdf`；[ACL Anthology](https://aclanthology.org/2025.acl-long.779/)，[Apple 代码](https://github.com/apple/ml-agent-evaluator)。
- 阅读重点：方法的 domain assessment→按需 tool execution→final assessment，附录 C、D、N。
- 论文结果表明外部验证工具在部分领域显著改善判断，但错误 tool 选择也会造成失败，且工具不应机械地总是执行。
- 本项目采用：Investigator 自主按当前 claim 选择 Tool；Judge 只提出信息缺口，不直接无限检索；工具均有 typed schema 和业务审计。
- 本项目没有照搬：Web/code/math validators；这里只封装当前 canonical/TLR/BM25/Dense 能力。

### Automatic Instantiation of Assurance Cases from Patterns Using Large Language Models（JSS 2025）

- 原文：`assurance-case-patterns.pdf`；[DOI](https://doi.org/10.1016/j.jss.2025.112353)，[arXiv](https://arxiv.org/abs/2410.05488)。
- 阅读重点：第 4 节四阶段方法，尤其 predicate-based element/relationship constraints。
- 论文先把 assurance case pattern 的元素、关系和 cardinality 形式化，再让 LLM 实例化；结果也指出模型会误解 pattern-specific relationships，半自动方案更现实。
- 本项目采用：LLM 只产出 Claim/Evidence/Verdict 的结构化字段；后端按固定
  `Requirement→Claim→Supporting/Counter Evidence→Verdict→Gap` 模式确定性组树。
- 本项目没有实现完整 GSN/SACM，也不让 LLM自由生成边。

### OpenAI Agents SDK 官方资料

- [SDK 总览](https://developers.openai.com/api/docs/guides/agents/sdk)
- [Agent 定义](https://developers.openai.com/api/docs/guides/agents/define-agents)
- [Models and providers](https://developers.openai.com/api/docs/guides/agents/models)
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

官方文档将 server-owned tools/state/storage 与 SDK agent loop 作为 code-first 路径，并支持
Pydantic `output_type`、function tools、Runner、context、handoff 和 tracing。本项目使用
Python SDK `0.22.3` 的 Agent、Runner、function tools、Pydantic structured output 与
`OpenAIChatCompletionsModel`；稳定顺序采用代码 orchestration，不使用 handoff。由于模型和
文档必须完全本地，SDK tracing 显式关闭，改存不含 chain-of-thought 的业务 audit events。

## 保守边界与 unresolved

- 新检索到的 supporting node 必须经过已有 FineTraceValidator 并得到 direct，才能成为 verified support。
- Counter-evidence 是 canonical exact quote，但现有 TLR validator 没有“contradiction”关系；当前保存为 `canonical_only`，交由 Judge 明确引用。没有依据扩展 TLR relation taxonomy。
- 未建立人工 SRS→SDS assessment ground truth，因此不做质量分数、权重或最优预算声明。
- 本轮没有可安全并行启动的本地生成模型，论文原则和 SDK wiring 已由 deterministic/fake E2E 验证，但模型行为未验证。
