"""Safe actionable diagnostics; never expose provider payloads, URLs or keys."""

from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider


class PreprocessingFailure(ValueError):
    pass


class ConfigurationMissing(ValueError):
    pass


def preflight(embedding, llm):
    missing = []
    if isinstance(embedding, OpenAICompatibleEmbeddingProvider) and not embedding._api_key:
        missing.append("MODEL_API_KEY")
    if isinstance(llm, OpenAICompatibleLLMProvider) and not llm._api_key:
        missing.append("LLM_API_KEY")
    if missing:
        raise ConfigurationMissing("未配置 " + ", ".join(missing) + "；请编辑后端 .env 并重启服务")


def failure_detail(exc):
    if isinstance(exc, (ConfigurationMissing, PreprocessingFailure)):
        message = str(exc)
    elif isinstance(exc, TimeoutError):
        message = "TLR 超时；请减小批次、Top-k 或检查模型服务延迟"
    elif isinstance(exc, ValueError):
        message = "输入或模型输出校验失败；检查分割策略、单元长度和证据引文"
    else:
        message = "模型或运行服务失败；检查模型地址、密钥、模型名称、网络及后端日志"
    return {"error_type": type(exc).__name__, "message": message}
