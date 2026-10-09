# Information tools: primary sources checked (2026-10-01)

- OpenAI official [Agents SDK overview](https://developers.openai.com/api/docs/guides/agents/sdk): the SDK owns the agent loop and invokes application-supplied tools. This change supplies business tool functions but does not claim an SDK Agent/Runner integration.
- OpenAI official [models and providers](https://developers.openai.com/api/docs/guides/agents/models) and [running agents](https://developers.openai.com/api/docs/guides/agents/running-agents): model/provider behavior and run state require separate integration decisions. Existing OpenAI-compatible chat completion support is not evidence that local Qwen/vLLM supports every SDK tool or structured-output feature.
- [BM25S project](https://github.com/xhluca/bm25s) and [official documentation](https://bm25s.github.io/): current project uses `BM25(method="lucene")`, `index`, then `retrieve` returning document indices and total scores. Tool code reuses those calls. No per-term score attribution is invented.
- [jieba-py maintained fork](https://github.com/bukun/jieba-py): current `ChineseTokenizer` calls `cut_for_search`, strips and lowercases tokens. Tool terms/frequencies are computed directly from that output.
- Project implementation `app/modules/traceability/retrievers.py`: source of truth for the exact current BM25 tokenizer, Lucene variant, and ranking path.
- Project implementation `app/modules/artifacts/models.py`, `app/modules/traceability/models.py`: source of truth for canonical/Docling facts, annotations, projections, candidates, links and run state.

Not established in this implementation: a supported per-term BM25 contribution API for the installed release; local vLLM tool-calling compatibility; production Session/PostgreSQL lifecycle; parse-failure-region persistence. These are not represented as validated capabilities.
