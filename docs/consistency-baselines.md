# 一致性模块基线与差异

先核对论文入口、公开实现和解析器文档再实现适配。日志中的 AI 摘要仅作为检索线索。未核实 Spec2Code 仓库维护状态，不能认定它已过期；本模块不依赖其旧运行环境。

| 基线 | 复用思路/工具 | 本项目差异 |
| --- | --- | --- |
| [Spec2Code，ASE 2025 官方页面](https://conf.researchr.org/details/ase-2025/ase-2025-papers/198/SPEC2CODE-Mapping-Software-Specification-to-Function-Level-Code-Implementation)；[作者论文](https://www.jianjunchen.com/p/rfc2code.ase25.pdf) | 映射先于一致性判断，函数表示及上下文有助于细粒度关联 | 使用现有 TLR；通用需求缺少 RFC 的统一约束结构，不强制 Trigger/Action 或 constraint-level Full/Partial 评分 |
| [DocPrism 论文](https://arxiv.org/abs/2511.00215)；[实现](https://github.com/SnowPhoebe/DocPrism) | 已下载并阅读原提示词，分别询问未实现内容、直接不符和未文档化行为，并要求文档/代码引文 | 从函数—文档单对扩展为需求—多文件证据；后端校验引文，额外行为不自动判错，疑似遗漏保留未决 |
| [Agent-as-a-Judge](https://github.com/metauto-ai/agent-as-a-judge) | workspace 图、定位、检索、读取与判定式评估思路 | 借其围绕需求补取证据的流程，未引入完整 Agent 运行时，不声称复制论文实验；补读限定数据库快照，最多两轮 |
| 已有 LiSSA/TLR 适配，见 `integrations/lissa/dependency.json` | 制品、片段偏移、候选判定、模型配置及租户/项目作用域 | 只读 TLR 结果，不修改 LiSSA 分类器；多个需求片段聚合回原需求制品 |
| [Python AST 文档](https://docs.python.org/3/library/ast.html)；[py-tree-sitter 文档](https://tree-sitter.github.io/py-tree-sitter/) | 语法树、包含关系和位置读取，复用已有解析器依赖 | 只做必要的源码层级，区分 UTF-8 字节与 Unicode 字符偏移，不编造跨文件调用解析 |

五类 verdict、表结构及保守判定门是本项目适配设计，不是上述论文已经验证的统一标准。当前没有足够实验数据给出本项目准确率，也没有可信基线支持对任意模糊需求输出客观实现百分比。

资料在 `artifacts/consistency/sources/`：原 HTML/README、DocPrism 原提示词与单例检测脚本、许可证、HTML 提取文本、哈希与下载清单。Spec2Code PDF 直接下载返回 HTTP 403，保存了网页工具可访问的提取结果 `spec2code-web-extract.json`，没有伪称下载成功。下载研究脚本没有作为运行依赖执行。

`manifest.json` 记录成功与失败；上一级的 `contracts.json` 保存接口及模型输出 JSON Schema。真实运行证据见 `outputs/consistency/` 和 `artifacts/consistency/smoke/`。
