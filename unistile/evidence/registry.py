"""Provider Registry —— 热插拔的落点。

Concept 不知道后端是谁；Binding 记录用了哪个 Provider；Registry 决定 Provider 实例。
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import DocumentEvidenceProvider, ProviderCapabilities
from .errors import BackendUnavailable


@dataclass
class Registration:
    provider: DocumentEvidenceProvider
    enabled: bool = True


class ProviderRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Registration] = {}

    def register(self, provider: DocumentEvidenceProvider, *, enabled: bool = True) -> None:
        pid = provider.capabilities().provider_id
        self._items[pid] = Registration(provider, enabled)

    def get(self, provider_id: str) -> DocumentEvidenceProvider:
        reg = self._items.get(provider_id)
        if reg is None:
            raise BackendUnavailable(f"Provider 未注册：{provider_id}")
        if not reg.enabled:
            raise BackendUnavailable(f"Provider 已登记但被关闭：{provider_id}（POC 期默认关闭）")
        return reg.provider

    def capabilities(self, provider_id: str) -> ProviderCapabilities:
        reg = self._items.get(provider_id)
        if reg is None:
            raise BackendUnavailable(f"Provider 未注册：{provider_id}")
        return reg.provider.capabilities()   # 能力声明可读，与是否启用无关

    def ids(self, *, enabled_only: bool = False) -> list[str]:
        return sorted(k for k, v in self._items.items() if v.enabled or not enabled_only)

    def is_enabled(self, provider_id: str) -> bool:
        reg = self._items.get(provider_id)
        return bool(reg and reg.enabled)
