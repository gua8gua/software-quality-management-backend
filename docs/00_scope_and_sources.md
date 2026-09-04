# 软件质量管理任务范围与资料初稿

## 1. 这个任务涉及的主要领域

- 需求工程 / Requirements Engineering
- 生命周期文档管理
- 软件配置管理 / 版本管理
- 需求追踪 / Traceability
- 代码分析 / 静态分析 / 代码搜索
- 测试管理 / 覆盖分析
- 缺陷管理 / 变更影响分析
- 软件质量度量 / Quality Gates
- 证据链与审计日志

## 2. 还需要补充的功能

- 项目 / 仓库 / 模块的统一建模
- 生命周期文档的导入与版本化
- 代码仓库导入与文件级索引
- 需求、设计、测试、代码之间的 trace link
- 文档一致性检查
- 文档与代码一致性检查
- 需求覆盖分析
- 质量规则配置
- 审批、批注、审计日志
- 评测集管理与结果回放

## 3. 可用数据集

| 名称                                          | 适合做什么                                          | 链接                                                                               |
| --------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------- |
| SEOSS 33                                      | 需求、缺陷报告、代码历史、trace link 的综合研究数据 | [论文/数据说明](https://doi.org/10.1145/3355140)                                    |
| fine-grained traceability replication package | 需求到代码的细粒度 traceability 研究基线            | [GitHub](https://github.com/SEOSS20/fine-grained-traceability)                      |
| CodeSearchNet                                 | 文档字符串与代码对齐、代码检索                      | [GitHub](https://github.com/github/CodeSearchNet)                                   |
| SWE-bench                                     | issue、补丁、测试、仓库代码联合分析                 | [网站](https://www.swebench.com/) / [GitHub](https://github.com/swe-bench/SWE-bench) |
| Defects4J                                     | 缺陷修复、测试、回归分析                            | [GitHub](https://github.com/rjust/defects4j)                                        |
| iTrust2                                       | 带需求、设计、代码、测试的教学型软件系统            | [GitHub](https://github.com/sen-uni-kn/itrust2)                                     |

## 4. 参考项目

| 项目                      | 参考价值                            | 链接                                                          |
| ------------------------- | ----------------------------------- | ------------------------------------------------------------- |
| SonarQube                 | 代码质量门禁、规则引擎、质量报告    | [GitHub](https://github.com/SonarSource/sonarqube)             |
| GitLab                    | 需求、代码、CI/CD、Issue 一体化协同 | [官网](https://about.gitlab.com/)                              |
| JanusTrace                | 需求到代码 traceability 工具        | [GitHub](https://github.com/anelyud/JanusTrace)                |
| fine-grained traceability | trace link 研究与实现基线           | [GitHub](https://github.com/SEOSS20/fine-grained-traceability) |

## 5. 参考论文

- SEOSS 33 数据集论文：`Software Engineering at Scale: A dataset of 33 open source software projects`
- CodeSearchNet 论文：`CodeSearchNet Challenge: Evaluating the State of Semantic Code Search`
- SWE-bench 论文：`SWE-bench: Can Language Models Resolve Real-World GitHub Issues?`
- 需求追踪综述：`Requirements Traceability: A Systematic Literature Review`

## 6. 下一步建议

1. 先把项目实体从“知识库”升级为“项目 / 仓库 / 版本”
2. 文档先支持 `txt / md / pdf / docx / html`
3. 代码先支持仓库导入和文件级索引
4. 再做 trace link、覆盖率和一致性检查


## 7. 需求文档需求分割
