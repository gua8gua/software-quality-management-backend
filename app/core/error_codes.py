"""统一错误码定义。

错误码分段规则（前后端通用）::

    0           成功
    10000~19999 通用 / 参数 / 鉴权
    20000~29999 接口 / HTTP / 请求
    30000~39999 数据库 / 存储
    40000~49999 大模型 LLM 专属
    50000~59999 系统 / 服务异常

对外提示（msg）：简洁、安全、不泄露技术细节；
对内排查上下文：在抛错时通过 ``data`` / 日志补充，绝不写进 msg。
"""

from __future__ import annotations

from enum import Enum

# 个别错误码需要覆盖默认 HTTP 状态码时在此声明（code -> http_status）
_HTTP_STATUS_OVERRIDES: dict[int, int] = {
    0: 200,
    10001: 422,  # 参数错误
    10002: 401,  # 未授权
    10003: 403,  # 权限不足
    10004: 404,  # 资源不存在
    10005: 405,  # 请求方法错误
    10006: 422,  # 数据格式不合法
    20002: 504,  # 接口请求超时
    20003: 429,  # 接口调用超限
    30002: 409,  # 数据已存在
    30003: 404,  # 数据不存在
    40004: 413,  # 上下文长度超限
    40005: 429,  # 大模型调用频率超限
}


def http_status_for(code: int) -> int:
    """根据错误码段位推导默认 HTTP 状态码。"""
    if code in _HTTP_STATUS_OVERRIDES:
        return _HTTP_STATUS_OVERRIDES[code]
    if 20000 <= code < 30000:  # 上游接口错误 -> 网关类错误
        return 502
    if 30000 <= code < 40000:  # 数据库错误
        return 500
    if 40000 <= code < 50000:  # 大模型错误 -> 上游错误
        return 502
    if 50000 <= code < 60000:  # 系统错误
        return 500
    return 400


class ErrorCode(Enum):
    """统一错误码枚举，``value = (code, 对外提示)``。"""

    # 成功
    SUCCESS = (0, "成功")

    # 通用 10000~19999
    PARAM_ERROR = (10001, "参数错误")
    UNAUTHORIZED = (10002, "未授权，请登录")
    FORBIDDEN = (10003, "权限不足")
    NOT_FOUND = (10004, "资源不存在")
    METHOD_ERROR = (10005, "请求方法错误")
    DATA_INVALID = (10006, "数据格式不合法")

    # 接口 / HTTP 20000~29999
    API_REQUEST_ERROR = (20001, "接口请求失败")
    API_TIMEOUT = (20002, "接口请求超时")
    API_RATE_LIMIT = (20003, "接口调用超限")

    # 数据库 30000~39999
    DB_ERROR = (30001, "数据库操作失败")
    DATA_DUPLICATE = (30002, "数据已存在")
    DATA_NOT_FOUND = (30003, "数据不存在")

    # 大模型 LLM 40000~49999
    LLM_ERROR = (40000, "大模型调用失败")
    LLM_TIMEOUT = (40001, "大模型响应超时")
    LLM_NO_RESPONSE = (40002, "大模型未返回有效内容")
    LLM_CONTENT_VIOLATION = (40003, "内容违规，大模型拒绝生成")
    LLM_TOKEN_LIMIT = (40004, "上下文长度超限")
    LLM_RATE_LIMIT = (40005, "大模型调用频率超限")
    LLM_MODEL_ERROR = (40006, "模型不存在或未部署")
    LLM_CONFIG_ERROR = (40007, "大模型配置错误")

    # 系统 / 服务 50000~59999
    SERVER_ERROR = (50000, "服务器异常")
    SYSTEM_BUSY = (50001, "系统繁忙，请稍后再试")
    INDEXING_ERROR = (50002, "文档处理失败，请稍后重试")

    def __init__(self, code: int, msg: str) -> None:
        self.code = code
        self.msg = msg
        self.http_status = http_status_for(code)

    def __str__(self) -> str:  # 便于日志直接打印
        return f"{self.code}:{self.msg}"
