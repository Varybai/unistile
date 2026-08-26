"""Runtime 侧的跨系统校验：C4 stale、scope 越界、证据漂移、热插拔切主。

这些检查是 Provider 换成谁都必须成立的部分，因此不放在 Provider 契约测试里。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unistile.app import Runtime
from unistile.evidence.adapter import Evidence
from unistile.evidence.contract import (
    CostRecord,
    EvidenceSearchResult,
    Locator,
    OmissionInfo,
    ProviderCapabilities,
    RankedEvidenceCandidate,
)
from unistile.evidence.errors import BackendUnavailable, ScopeError
from unistile.resources.normalizer import text_sha256

BUNDLE = Path("knowledge")
AGR = "kn:agreement:AGR0048"


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


def test_end_to_end_evidence_is_original_resource(rt):
    b = rt.adapter.search(concept_uids=[AGR], query="质保期限")
    assert b.evidence and b.rejected == []
    assert b.max_level() == "original-resource"
    e = b.evidence[0]
    assert e.locator.section_path[-1] == "7.2 质保期限"
    assert "12 个月" in e.evidence_text


def test_governed_relation_hop_finds_amendment(rt):
    scope = rt.expand_scope([AGR])
    assert "kn:agreement:Supplement-02" in scope
    b = rt.adapter.search(concept_uids=scope, query="质保期限")
    texts = " ".join(e.evidence_text for e in b.evidence)
    assert "24 个月" in texts, "沿 amends 展开后必须能看到补充协议的修改"


def test_without_hop_amendment_is_invisible(rt):
    b = rt.adapter.search(concept_uids=[AGR], query="质保期限")
    assert all(e.concept_uid == AGR for e in b.evidence)


# ---------- C4 Binding stale ----------
def test_stale_binding_is_rejected_not_silently_used(rt):
    row = rt.catalog.bindings_for([AGR])[0]
    rt.catalog.upsert_binding(
        binding_id=row.binding_id, concept_uid=row.concept_uid, resource_uri=row.resource_uri,
        resource_revision=row.resource_revision, provider_id=row.provider_id,
        provider_version=row.provider_version, backend_object_id=row.backend_object_id,
        source_sha256="sha256:NEWER", indexed_sha256=row.indexed_sha256, status="stale", role="primary",
    )
    b = rt.adapter.search(concept_uids=[AGR], query="质保期限")
    assert b.evidence == []
    assert b.rejected and all(r.reason_code == "binding.stale" for r in b.rejected)


# ---------- 证据漂移 ----------
def test_tampered_text_plane_breaks_readback(rt):
    b = rt.adapter.search(concept_uids=[AGR], query="质保期限")
    sha = b.evidence[0].locator.normalized_text_sha256
    p = Path(rt.resources.root) / sha.replace("sha256:", "") / "text.txt"
    p.write_text(p.read_text(encoding="utf-8").replace("12 个月", "99 个月"), encoding="utf-8")
    b2 = rt.adapter.search(concept_uids=[AGR], query="质保期限")
    assert any(r.reason_code == "locator.hash_mismatch" for r in b2.rejected)


# ---------- scope 越界的 Provider 必须被拦下 ----------
class RogueProvider:
    def capabilities(self):
        return ProviderCapabilities("rogue", "1.0.0", retrieval_modes=("keyword",),
                                    locator_kinds=("char_span",), supports_slice_readback=True)

    def bind(self, req): ...
    def reindex(self, binding_id): ...
    def unbind(self, binding_id): ...
    def expand(self, req): ...
    def health(self): ...

    def search(self, req):
        c = RankedEvidenceCandidate(
            candidate_id="rogue#1", binding_id="bind:SOMEONE-ELSE", concept_uid="kn:agreement:OTHER",
            resource_uri="asset://documents/other.md", resource_revision=1, text="越界内容",
            locator=Locator(kind="char_span", resource_uri="asset://documents/other.md", resource_revision=1,
                            char_start=0, char_end=4, content_sha256=text_sha256("越界内容")),
            score=9.9, score_kind="bm25", provider_id="rogue", provider_version="1.0.0",
        )
        return EvidenceSearchResult((c,), OmissionInfo(1, 1), CostRecord(calls=1))


def test_out_of_scope_candidate_is_rejected(rt):
    rt.registry.register(RogueProvider())
    row = rt.catalog.bindings_for([AGR])[0]
    rt.catalog.upsert_binding(
        binding_id="bind:rogue", concept_uid=AGR, resource_uri=row.resource_uri, resource_revision=1,
        provider_id="rogue", provider_version="1.0.0", backend_object_id="x",
        source_sha256="s", indexed_sha256="s", status="ready", role="shadow",
    )
    b = rt.adapter.search(concept_uids=[AGR], query="质保期限", role="shadow")
    assert b.evidence == []
    assert b.rejected[0].reason_code == "scope.violation"


# ---------- 热插拔：切主只改 role ----------
def test_hot_swap_by_role_switch(rt):
    from unistile.evidence.contract import BindRequest

    row = rt.catalog.bindings_for([AGR])[0]
    null = rt.registry.get("null")
    null_binding = f"{row.binding_id}:null"
    null.bind(BindRequest(binding_id=null_binding, concept_uid=AGR, resource_uri=row.resource_uri,
                          resource_revision=row.resource_revision, media_type="text/markdown",
                          source_sha256=row.source_sha256, normalized_text_sha256="sha256:x", text=""))
    rt.catalog.upsert_binding(
        binding_id=null_binding, concept_uid=AGR, resource_uri=row.resource_uri,
        resource_revision=row.resource_revision, provider_id="null", provider_version="1.0.0",
        backend_object_id="null:x", source_sha256=row.source_sha256, indexed_sha256=row.source_sha256,
        status="ready", role="shadow",
    )
    # 影子期：primary 仍是 local-fts，回答不受影响
    assert rt.adapter.search(concept_uids=[AGR], query="质保期限").evidence

    # 切主：只改两行 role，不动任何 Concept 文件
    rt.catalog.set_role(row.binding_id, "shadow")
    rt.catalog.set_role(null_binding, "primary")
    after = rt.adapter.search(concept_uids=[AGR], query="质保期限")
    assert after.evidence == []
    assert after.omissions[0]["provider_id"] == "null"

    # 回滚同样是两行
    rt.catalog.set_role(null_binding, "shadow")
    rt.catalog.set_role(row.binding_id, "primary")
    assert rt.adapter.search(concept_uids=[AGR], query="质保期限").evidence


def test_disabled_provider_cannot_be_used_but_capabilities_readable(rt):
    assert rt.registry.capabilities("weknora").query_expansion == "agentic"
    with pytest.raises(BackendUnavailable):
        rt.registry.get("weknora")


def test_structured_concept_has_no_document_binding(rt):
    assert rt.catalog.bindings_for(["kn:equipment:A-1007"]) == []
    with pytest.raises(ScopeError):
        rt.adapter.search(concept_uids=["kn:equipment:A-1007"], query="额定功率")


def test_runtime_dir_is_rebuildable(tmp_path, bundle):
    import shutil

    rdir = tmp_path / "runtime"
    r1 = Runtime(bundle, rdir)
    r1.ingest()
    before = [e.locator.to_stable_dict() for e in r1.adapter.search(concept_uids=[AGR], query="质保期限").evidence]
    r1.close()
    shutil.rmtree(rdir)

    r2 = Runtime(bundle, rdir)
    r2.ingest()
    after = [e.locator.to_stable_dict() for e in r2.adapter.search(concept_uids=[AGR], query="质保期限").evidence]
    r2.close()
    assert before == after, "删除 runtime/ 后必须能从 Canonical + Resource 完整重建"
