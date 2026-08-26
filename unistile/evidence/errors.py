"""跨 Provider 稳定的错误分类。Provider 不得抛出裸异常。

BindingStale 尤其重要：返回过期索引的旧结果比报错更危险，必须显式暴露。
"""

from __future__ import annotations


class ProviderError(Exception):
    """所有 Provider 错误的基类。"""

    retryable = False


class ScopeError(ProviderError):
    """scope 为空、binding 不存在或越权。"""


class BindingStale(ProviderError):
    """source_sha256 != indexed_sha256，索引落后于资源。"""


class UnsupportedCapability(ProviderError):
    """请求用到了 capabilities() 未声明的能力。"""


class BudgetExceeded(ProviderError):
    """超出 rounds/candidates/latency 预算。"""


class BackendUnavailable(ProviderError):
    """后端不可达。"""

    retryable = True


class NotImplementedProvider(ProviderError):
    """Provider 已登记能力声明，但该方法尚未实现（如 POC 期的 WeKnora）。"""


class ProviderInternal(ProviderError):
    """其他内部错误，不可重试，进 Trace。"""
