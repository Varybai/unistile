"""DocumentEvidenceAdapter —— Runtime 侧，Provider 无关。

它做的是"跨系统无法由检索分数替代的检查"：
  - candidate 是否来自允许的 scope_binding_ids
  - Binding 的 source_sha256 与当前 Concept/Resource 版本是否一致
  - locator 是否能在归一化文本平面上精确回读且内容哈希一致
  - 证据等级是否达到任务门槛

只有通过这些检查，RankedEvidenceCandidate --validated_as--> Evidence。
Provider 的排序分数只影响候选顺序，永远不能直接建立 supports。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..catalog.store import BindingRow, CatalogStore
from ..resources.normalizer import ResourceStore, text_sha256
from .contract import (
    EvidenceLevel,
    EvidenceSearchRequest,
    Locator,
    ProviderWarning,
    RankedEvidenceCandidate,
    SearchBudget,
    SearchFilters,
)
from .errors import ScopeError
from .registry import ProviderRegistry

LEVEL_ORDER: dict[EvidenceLevel, int] = {
    "provider_opaque": 0,
    "derived-chunk": 1,
    "original-resource": 2,
}


@dataclass(frozen=True)
class Evidence:
    concept_uid: str
    resource_uri: str
    resource_revision: int
    source_sha256: str
    locator: Locator
    evidence_text: str
    evidence_level: EvidenceLevel
    provider_id: str
    provider_version: str
    score: float
    score_kind: str
    verified_at: str | None = None      # 检索到 != 已核验


@dataclass(frozen=True)
class RejectedCandidate:
    candidate_id: str
    reason_code: str
    reason: str


@dataclass
class EvidenceBundle:
    evidence: list[Evidence] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)
    warnings: list[ProviderWarning] = field(default_factory=list)
    omissions: list[dict] = field(default_factory=list)
    cost: dict = field(default_factory=dict)

    def max_level(self) -> EvidenceLevel:
        if not self.evidence:
            return "provider_opaque"
        return max((e.evidence_level for e in self.evidence), key=lambda l: LEVEL_ORDER[l])


class DocumentEvidenceAdapter:
    def __init__(self, catalog: CatalogStore, registry: ProviderRegistry, resources: ResourceStore):
        self.catalog = catalog
        self.registry = registry
        self.resources = resources

    # ---------- 回读：不依赖任何 Provider 存活 ----------
    def fetch_slice(self, loc: Locator) -> str:
        if loc.kind != "char_span" or loc.char_start is None or loc.char_end is None:
            raise ScopeError(f"locator.kind={loc.kind} 无法精确回读；证据等级封顶 derived-chunk")
        if not loc.normalized_text_sha256:
            raise ScopeError("locator 缺少 normalized_text_sha256，无法定位文本平面")
        return self.resources.read_slice(loc.normalized_text_sha256, loc.char_start, loc.char_end)

    # ---------- 检索 + 校验 ----------
    def search(
        self,
        *,
        concept_uids: Sequence[str],
        query: str,
        obligation_ids: Sequence[str] = (),
        budget: SearchBudget = SearchBudget(),
        role: str = "primary",
        provider_id: str | None = None,
        section_prefix: tuple[str, ...] = (),
    ) -> EvidenceBundle:
        bindings = self.catalog.bindings_for(concept_uids, provider_id=provider_id, role=role)
        if not bindings:
            raise ScopeError(
                f"concept {list(concept_uids)} 在 role={role} 下没有任何 Binding；"
                " 文档证据动作必须使用非空 scope_binding_ids"
            )
        by_provider: dict[str, list[BindingRow]] = {}
        for b in bindings:
            by_provider.setdefault(b.provider_id, []).append(b)

        bundle = EvidenceBundle()
        for pid, rows in sorted(by_provider.items()):
            provider = self.registry.get(pid)
            caps = provider.capabilities()
            scope = tuple(b.binding_id for b in rows)
            result = provider.search(
                EvidenceSearchRequest(
                    scope_binding_ids=scope,
                    query=query,
                    obligation_ids=tuple(obligation_ids),
                    budget=budget,
                    filters=SearchFilters(section_prefix=section_prefix) if section_prefix else None,
                )
            )
            bundle.warnings.extend(result.warnings)
            bundle.omissions.append(
                {"provider_id": pid, **{k: v for k, v in result.omission.__dict__.items() if v is not None}}
            )
            bundle.cost[pid] = result.actual_cost.__dict__

            index = {b.binding_id: b for b in rows}
            for c in result.candidates:
                ev, rej = self._validate(c, index, caps_max_level=caps.max_evidence_level, caps_rerank=caps.rerank)
                if ev:
                    bundle.evidence.append(ev)
                if rej:
                    bundle.rejected.append(rej)
        bundle.evidence.sort(key=lambda e: e.score, reverse=True)
        return bundle

    def _validate(
        self,
        c: RankedEvidenceCandidate,
        scope_index: dict[str, BindingRow],
        *,
        caps_max_level: EvidenceLevel,
        caps_rerank: bool,
    ) -> tuple[Evidence | None, RejectedCandidate | None]:
        # 1. scope 越界：Provider 返回了不属于请求范围的 binding
        binding = scope_index.get(c.binding_id)
        if binding is None:
            return None, RejectedCandidate(
                c.candidate_id, "scope.violation", f"candidate 来自 scope 外的 binding：{c.binding_id}"
            )

        # 2. 能力诚实：未声明 rerank 却填了 rerank_score
        if c.rerank_score is not None and not caps_rerank:
            return None, RejectedCandidate(
                c.candidate_id, "capability.dishonest", "Provider 未声明 rerank 却返回了 rerank_score"
            )

        level: EvidenceLevel = caps_max_level

        # 3. Binding stale：索引落后于资源，不得静默当作当前证据
        if binding.is_stale:
            return None, RejectedCandidate(
                c.candidate_id,
                "binding.stale",
                f"binding {binding.binding_id} 索引落后（indexed={binding.indexed_sha256}"
                f" != source={binding.source_sha256}）；需 refresh 后重试",
            )

        # 4. 回读校验：locator 必须能在文本平面上复现同样的内容
        if LEVEL_ORDER[level] >= LEVEL_ORDER["original-resource"]:
            try:
                slice_text = self.fetch_slice(c.locator)
            except (ScopeError, FileNotFoundError, ValueError) as e:
                return None, RejectedCandidate(c.candidate_id, "locator.unreadable", str(e))
            if c.locator.content_sha256 and text_sha256(slice_text) != c.locator.content_sha256:
                return None, RejectedCandidate(
                    c.candidate_id,
                    "locator.hash_mismatch",
                    "回读内容与 locator.content_sha256 不一致，证据可能已漂移",
                )
            text = slice_text
        else:
            text = c.text

        return (
            Evidence(
                concept_uid=c.concept_uid,
                resource_uri=c.resource_uri,
                resource_revision=c.resource_revision,
                source_sha256=binding.source_sha256,
                locator=c.locator,
                evidence_text=text,
                evidence_level=level,
                provider_id=c.provider_id,
                provider_version=c.provider_version,
                score=c.score,
                score_kind=c.score_kind,
            ),
            None,
        )
