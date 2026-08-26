"""null/v1 —— 无检索能力的 Provider。

存在的意义不是"占位"，而是让契约测试能验证失败路径真的成立：
检索不到 → obligation 停在 unseen → 门禁拒绝 answer → abstain。
这是整套 Obligation 设计最容易被绕过的地方；只有一个 Provider 时它无法被证伪。
"""

from __future__ import annotations

from ..contract import (
    BindRequest,
    BindResult,
    ContextExpandRequest,
    CostRecord,
    EvidenceSearchRequest,
    EvidenceSearchResult,
    HealthReport,
    OmissionInfo,
    ProviderCapabilities,
)
from ..errors import ScopeError

PROVIDER_ID = "null"
PROVIDER_VERSION = "1.0.0"


class NullProvider:
    def __init__(self) -> None:
        self._bound: set[str] = set()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=PROVIDER_ID,
            provider_version=PROVIDER_VERSION,
            query_expansion="none",
            retrieval_modes=(),
            locator_kinds=(),
            supports_slice_readback=False,
            determinism="deterministic",
            max_scope_size=256,
            media_types=("text/markdown", "text/plain"),
        )

    def bind(self, req: BindRequest) -> BindResult:
        self._bound.add(req.binding_id)
        return BindResult(req.binding_id, f"null:{req.binding_id}", req.source_sha256, "ready", 0)

    def reindex(self, binding_id: str) -> BindResult:
        return BindResult(binding_id, f"null:{binding_id}", "", "ready", 0)

    def unbind(self, binding_id: str) -> None:
        self._bound.discard(binding_id)

    def search(self, req: EvidenceSearchRequest) -> EvidenceSearchResult:
        if not req.scope_binding_ids:
            raise ScopeError("scope_binding_ids 为空")
        unknown = [b for b in req.scope_binding_ids if b not in self._bound]
        if len(unknown) == len(req.scope_binding_ids):
            raise ScopeError(f"scope 内没有任何已绑定的 binding：{unknown}")
        return EvidenceSearchResult(
            candidates=(),
            omission=OmissionInfo(0, 0, reason="null provider 未声明任何检索能力，永远返回空候选"),
            actual_cost=CostRecord(calls=1),
        )

    def expand(self, req: ContextExpandRequest) -> EvidenceSearchResult:
        raise ScopeError(f"null provider 不持有任何候选：{list(req.candidate_ids)}")

    def health(self) -> HealthReport:
        return HealthReport(PROVIDER_ID, True, f"{len(self._bound)} bindings registered, 0 indexed")
