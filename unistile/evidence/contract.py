"""DocumentEvidenceProvider 契约 —— 系统里唯一的可插拔单元。

不可插拔（属于 Runtime，永远只有一套）：权限与 Scope 过滤、Obligation 状态机与
answer 门禁、Evidence Envelope 结构、Concept/Binding 身份解析。

三条硬规则：
  1. Runtime 只允许依赖 capabilities() 已声明的能力；缺失即降级或 obligation=blocked。
  2. minimum_evidence_level=original-resource 要求 supports_slice_readback=True。
  3. 能力声明必须诚实：声明 rerank=False 就不得填 rerank_score。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

CONTRACT_VERSION = "unistile/evidence-contract/v1"

QueryExpansion = Literal["none", "multi_query", "agentic"]
RetrievalMode = Literal["keyword", "vector", "hybrid"]
Diversity = Literal["none", "mmr"]
ImageUnderstanding = Literal["none", "ocr_caption", "native_multimodal"]
Determinism = Literal["deterministic", "stochastic"]
LocatorKind = Literal["char_span", "page", "section_path", "chunk_id", "bbox", "time_span", "provider_opaque"]
EvidenceLevel = Literal["provider_opaque", "derived-chunk", "original-resource"]


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    provider_version: str
    query_expansion: QueryExpansion = "none"
    retrieval_modes: tuple[RetrievalMode, ...] = ()
    rerank: bool = False
    diversity: Diversity = "none"
    parent_child_expansion: bool = False
    neighbor_expansion: bool = False
    image_understanding: ImageUnderstanding = "none"
    locator_kinds: tuple[LocatorKind, ...] = ()
    supports_slice_readback: bool = False
    determinism: Determinism = "deterministic"
    max_scope_size: int = 64
    max_rounds: int = 1
    media_types: tuple[str, ...] = ()

    @property
    def max_evidence_level(self) -> EvidenceLevel:
        """能力决定该 Provider 的候选最高能升到哪一级证据。"""
        if self.supports_slice_readback and "char_span" in self.locator_kinds:
            return "original-resource"
        if self.locator_kinds:
            return "derived-chunk"
        return "provider_opaque"


@dataclass(frozen=True)
class Locator:
    """跨 Provider 可比较的最小集合。native 不参与比较，也不进 Evidence Envelope 稳定字段。"""

    kind: LocatorKind
    resource_uri: str
    resource_revision: int
    normalized_text_sha256: str | None = None
    extractor_version: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    page: int | None = None
    section_path: tuple[str, ...] = ()
    content_sha256: str | None = None
    native: dict[str, Any] = field(default_factory=dict)

    def to_stable_dict(self) -> dict[str, Any]:
        d = {
            "kind": self.kind,
            "resource_uri": self.resource_uri,
            "resource_revision": self.resource_revision,
            "normalized_text_sha256": self.normalized_text_sha256,
            "extractor_version": self.extractor_version,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page": self.page,
            "section_path": list(self.section_path),
            "content_sha256": self.content_sha256,
        }
        return {k: v for k, v in d.items() if v not in (None, [], ())}


@dataclass(frozen=True)
class CostRecord:
    tokens: int = 0
    calls: int = 0
    latency_ms: int = 0
    evidence_reads: int = 0


@dataclass(frozen=True)
class OmissionInfo:
    """Agent 不能把"没有展示"误认为"不存在"。"""

    total_matched: int = 0
    returned: int = 0
    truncated_by: str | None = None  # budget/max_candidates/scope
    reason: str | None = None
    next_cursor: str | None = None


@dataclass(frozen=True)
class ProviderWarning:
    code: str
    message: str
    binding_id: str | None = None


@dataclass(frozen=True)
class RankedEvidenceCandidate:
    candidate_id: str
    binding_id: str
    concept_uid: str
    resource_uri: str
    resource_revision: int
    text: str
    locator: Locator
    score: float
    score_kind: Literal["bm25", "cosine", "rrf", "rerank", "opaque"]
    provider_id: str
    provider_version: str
    rerank_score: float | None = None
    native: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchBudget:
    max_candidates: int = 10
    max_rounds: int = 1
    latency_ms: int = 15000
    tokens: int = 4000


@dataclass(frozen=True)
class SearchFilters:
    section_prefix: tuple[str, ...] = ()
    page_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class EvidenceSearchRequest:
    scope_binding_ids: tuple[str, ...]      # 非空；由 Runtime 从 concept_uid 解析而来
    query: str
    obligation_ids: tuple[str, ...] = ()    # 不透明透传，仅用于 Trace 归因
    as_of: str | None = None
    budget: SearchBudget = SearchBudget()
    filters: SearchFilters | None = None


@dataclass(frozen=True)
class EvidenceSearchResult:
    candidates: tuple[RankedEvidenceCandidate, ...]
    omission: OmissionInfo
    actual_cost: CostRecord
    warnings: tuple[ProviderWarning, ...] = ()


@dataclass(frozen=True)
class ContextExpandRequest:
    candidate_ids: tuple[str, ...]
    direction: Literal["parent", "neighbor", "full"] = "neighbor"
    span: int = 1


@dataclass(frozen=True)
class BindRequest:
    binding_id: str
    concept_uid: str
    resource_uri: str
    resource_revision: int
    media_type: str
    source_sha256: str
    normalized_text_sha256: str
    text: str
    outline: tuple[tuple[tuple[str, ...], int, int], ...] = ()
    # (页码, start, end)。空 = 抽取器不知道页边界（如 anydoc）——
    # 此时 locator.page 缺省，不得填 1 冒充。
    pages: tuple[tuple[int, int, int], ...] = ()   # (section_path, start, end)
    extractor_version: str = "normalizer-v1"


@dataclass(frozen=True)
class BindResult:
    binding_id: str
    backend_object_id: str
    indexed_sha256: str
    status: Literal["ready", "pending", "failed"]
    units: int = 0


@dataclass(frozen=True)
class ResourceSlice:
    text: str
    content_sha256: str
    locator: Locator


@dataclass(frozen=True)
class HealthReport:
    provider_id: str
    healthy: bool
    detail: str = ""


@runtime_checkable
class DocumentEvidenceProvider(Protocol):
    """在已收敛的资源范围内，返回带 locator 的排序证据候选，并支持按 locator 精确回读。"""

    def capabilities(self) -> ProviderCapabilities: ...

    def bind(self, req: BindRequest) -> BindResult: ...

    def reindex(self, binding_id: str) -> BindResult: ...

    def unbind(self, binding_id: str) -> None: ...

    def search(self, req: EvidenceSearchRequest) -> EvidenceSearchResult: ...

    def expand(self, req: ContextExpandRequest) -> EvidenceSearchResult: ...

    def health(self) -> HealthReport: ...
