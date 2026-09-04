"""通用大模型调用抽象。

后续所有需要调用大模型的能力（重排、query rewrite、意图识别、评测辅助生成等）
都必须通过 :class:`LLMProvider` 访问模型，不允许在业务 service 或具体能力 provider
里直接写 HTTP 请求。
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """统一大模型调用接口。

    实现类只负责「模型调用 + JSON 解析」，不负责具体业务逻辑。
    """

    #: provider 名称，例如 ``openai_compatible``，用于日志与接口元信息。
    name: str = "base"

    #: 使用的模型名称，用于日志与接口元信息。
    model: str = ""

    @abstractmethod
    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> dict:
        """以 chat completion 方式调用大模型并返回解析后的 JSON 对象。

        参数
        ----
        system_prompt:
            系统提示词，定义任务、约束和输出格式。
        user_payload:
            结构化 JSON 输入（会被序列化为 user 消息）。
        temperature:
            采样温度。重排、意图识别等确定性任务建议使用 ``0.0``。
        timeout_seconds:
            单次调用超时时间；为 ``None`` 时使用 provider 默认值。

        返回
        ----
        解析后的 JSON ``dict``。

        异常
        ----
        模型未返回合法 JSON 或调用失败时抛出
        :class:`~app.core.exceptions.LLMProviderError`。
        """
