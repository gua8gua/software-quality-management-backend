# Agent Evaluation 未解决问题

## LOCAL_LLM_RESOURCE_STARTUP_FAILED：单卡无法在保守显存预算下同时运行现有 Embedding 与 Qwen3.5-9B

- **问题**：服务器的单张 RTX 4090 D（24,564 MiB）无法在为现有 Qwen3 Embedding 保留显存和运行余量的前提下，以本轮保守配置启动本地 Qwen3.5-9B。因此无法执行依赖真实生成式 LLM 的 TLR Fine Validation 和 Requirement Agent Evaluation。
- **触发案例**：2026-09-28 使用服务器已有 `vllm/vllm-openai:v0.25.0` 镜像和完整本地 `Qwen/Qwen3.5-9B` 权重，设置 `max-model-len=16384`、`gpu-memory-utilization=0.75`，同时保持 `qwen3-embedding-0.6b-1024` 运行。
- **当前观察**：模型四个 safetensors 分片均成功读取；vLLM 报告权重加载占用 16.8 GiB，但初始化 KV cache 时只剩 `-0.22 GiB` 可用空间，随后以 `No available memory for the cache blocks` 退出。不是 CUDA OOM。临时容器重启 2 次后按安全规则删除，OvisOCR2 已恢复，Embedding、后端和 PostgreSQL 均保持 healthy。
- **为什么现有实现无法可靠解决**：`gpu-memory-utilization=0.75` 已接近本轮为单卡共存选择的保守上限；继续提高会压缩 Embedding 和系统余量。降低上下文长度不会消除 vLLM 至少需要正 KV cache 的条件。量化、CPU offload、替换模型、暂停 Embedding 或迁移到多 GPU 均属于需要单独评估的部署决策，本轮明确禁止为了跑通而采用。
- **已查阅的实现/资料**：服务器既有 `/opt/qwen-vllm/docker-compose.yml`、工具调用 override、Docker inspect 结果、vLLM 0.25.0 实际启动日志，以及异常中指向的 vLLM memory conservation 官方文档。资源问题没有使用论文推导或自定义算法。
- **可能方向**：在独立任务中评估额外 GPU、为生成模型保留独占 GPU、经授权的量化模型、或在不要求并行服务时制定 Embedding/LLM 的调度策略。任何方案都应先测量 KV cache、最大并发和真实 Agent 上下文需求。
- **当前保守处理**：维持原有 Qwen3 Embedding 与 OvisOCR2 服务；Qwen3.5-9B 保持停止；禁止回退到 DeepSeek/OpenAI 公网 API；不生成 Fake Agent 结果；本轮不执行真实 TLR/Agent 全流程。

- **状态更新（2026-09-29）**：该资源阻塞已解除。Embedding 已迁移到本机 RTX 5060 Ti，服务器旧 Embedding/OvisOCR2 已停止，Qwen3.5-9B 以 `max-model-len=16384`、`gpu-memory-utilization=0.88` 成功启动并完成真实调用。保留本条作为历史诊断记录。

## JUDGE_CROSS_FIELD_INCONSISTENCY：结构合法但结论字段可能互相矛盾

- **Problem**：结构化 schema 能约束字段类型和枚举，但不能保证 `claim_verdicts` 与 `requirement_verdict` 的跨字段逻辑一致，也不能保证同一证据节点不会同时出现在 supporting/counter 集合。
- **Observed Case**：2026-09-29 的本地 Qwen3.5-9B Judge smoke 中，唯一 claim 被判为 `contradicted`，同一输出却把 requirement 汇总为 `supported`，并把反证节点同时放入 `supporting_node_ids` 与 `counter_node_ids`。
- **Current Evidence**：vLLM HTTP、Qwen parser、OpenAI Agents SDK structured output 和 Pydantic 字段解析均成功；异常属于模型生成内容的逻辑一致性，而非 JSON/parser/schema 故障。
- **Root-cause hypothesis**：JSON Schema 只表达局部结构约束，当前 Judge 输出模型没有可表达这些条件依赖的跨字段 invariant；9B 模型在汇总阶段发生标签反转。
- **Relevant paper/project**：OpenAI Agents SDK structured output/Pydantic output type；Qwen3.5 官方 vLLM non-thinking 与 structured-output 调用方式。两者解决结构约束，不承诺业务结论一致性。
- **Conservative handling**：不自动把矛盾结果改写成 `supported` 或 `contradicted`，不引入 JSON repair/第二模型投票；真实 Evaluation 结果出现该模式时保留 audit trail，并把该 requirement 标为需人工复核。
- **Future options**：建立人工 trace/evaluation set 后，评估 deterministic cross-field validator + bounded retry；在没有标注验证前不宣称该策略能提升判定准确率。
