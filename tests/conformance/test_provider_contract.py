"""C1–C8：Provider 无关的契约测试，同一套跑所有已注册 Provider。

这套测试的价值不在覆盖率，而在于它对每个 Provider 提同样的问题：
范围守得住吗、locator 回读得到吗、能力声明诚实吗、失败分类对吗。
"""

from __future__ import annotations

import pytest

from unistile.evidence.contract import (
    ContextExpandRequest,
    EvidenceSearchRequest,
    SearchBudget,
)
from unistile.evidence.errors import NotImplementedProvider, ProviderError, ScopeError
from unistile.resources.normalizer import text_sha256

QUERY = "质保期限"


def _search(provider, scope, query=QUERY, limit=10):
    return provider.search(
        EvidenceSearchRequest(
            scope_binding_ids=tuple(scope), query=query, budget=SearchBudget(max_candidates=limit)
        )
    )


# ---------- C1 Scope 强制 ----------
def test_c1_empty_scope_raises(provider):
    with pytest.raises(ScopeError):
        _search(provider, ())


def test_c1_unknown_scope_raises(provider):
    with pytest.raises(ScopeError):
        _search(provider, ("bind:does-not-exist",))


def test_c1_no_leakage_outside_scope(provider, corpus):
    only = corpus["kn:agreement:AGR0048"].binding_id
    res = _search(provider, (only,))
    assert all(c.binding_id == only for c in res.candidates)


# ---------- C2 Locator 回读 ----------
def test_c2_locator_readback_matches_hash(provider, corpus, resource_store):
    caps = provider.capabilities()
    if not caps.supports_slice_readback:
        pytest.skip(f"{caps.provider_id} 未声明 supports_slice_readback，证据等级封顶 derived-chunk")
    res = _search(provider, [r.binding_id for r in corpus.values()])
    assert res.candidates, "声明可回读的 Provider 至少应命中一段"
    for c in res.candidates:
        assert c.locator.kind == "char_span"
        text = resource_store.read_slice(
            c.locator.normalized_text_sha256, c.locator.char_start, c.locator.char_end
        )
        assert text_sha256(text) == c.locator.content_sha256


# ---------- C3 确定性 ----------
def test_c3_deterministic_results(provider, corpus):
    caps = provider.capabilities()
    if caps.determinism != "deterministic":
        pytest.skip(f"{caps.provider_id} 声明为 stochastic")
    scope = [r.binding_id for r in corpus.values()]
    a = [c.candidate_id for c in _search(provider, scope).candidates]
    b = [c.candidate_id for c in _search(provider, scope).candidates]
    assert a == b


# ---------- C5 空/退化 ----------
def test_c5_no_match_returns_empty_with_omission(provider, corpus):
    res = _search(provider, [r.binding_id for r in corpus.values()], query="量子纠缠退相干时间")
    assert res.candidates == ()
    assert res.omission.returned == 0


def test_c5_too_short_query_is_explicit(provider, corpus):
    res = _search(provider, [r.binding_id for r in corpus.values()], query="期")
    assert res.candidates == ()
    assert res.omission.reason, "退化查询必须给出原因，不能静默返回空"


# ---------- C6 错误分类 ----------
def test_c6_errors_are_typed(provider):
    with pytest.raises(ProviderError):
        _search(provider, ("bind:nope",))


def test_c6_expand_unknown_candidate(provider):
    with pytest.raises(ProviderError):
        provider.expand(ContextExpandRequest(candidate_ids=("no-such-candidate",)))


# ---------- C7 Budget ----------
def test_c7_budget_limits_candidates(provider, corpus):
    scope = [r.binding_id for r in corpus.values()]
    full = _search(provider, scope, limit=50)
    if len(full.candidates) < 2:
        pytest.skip("候选不足以验证截断")
    small = _search(provider, scope, limit=1)
    assert len(small.candidates) == 1
    assert small.omission.truncated_by == "max_candidates"
    assert small.omission.total_matched >= small.omission.returned


# ---------- C8 能力诚实 ----------
def test_c8_no_rerank_score_when_not_declared(provider, corpus):
    caps = provider.capabilities()
    res = _search(provider, [r.binding_id for r in corpus.values()])
    if not caps.rerank:
        assert all(c.rerank_score is None for c in res.candidates)


def test_c8_locator_kinds_are_declared(provider, corpus):
    caps = provider.capabilities()
    res = _search(provider, [r.binding_id for r in corpus.values()])
    for c in res.candidates:
        assert c.locator.kind in caps.locator_kinds


def test_c8_undeclared_expansion_direction_rejected(provider, corpus):
    caps = provider.capabilities()
    if caps.parent_child_expansion:
        pytest.skip("该 Provider 声明支持 parent 扩展")
    res = _search(provider, [r.binding_id for r in corpus.values()])
    if not res.candidates:
        pytest.skip("无候选可扩展")
    with pytest.raises(ProviderError):
        provider.expand(ContextExpandRequest(candidate_ids=(res.candidates[0].candidate_id,), direction="parent"))


def test_c8_score_kind_matches_modes(provider, corpus):
    caps = provider.capabilities()
    res = _search(provider, [r.binding_id for r in corpus.values()])
    for c in res.candidates:
        if "vector" not in caps.retrieval_modes and "hybrid" not in caps.retrieval_modes:
            assert c.score_kind != "cosine"


# ---------- 未实现的 Provider 必须响亮失败 ----------
def test_weknora_capabilities_readable_without_backend(weknora):
    caps = weknora.capabilities()
    assert caps.provider_version.endswith("unverified")
    assert caps.determinism == "stochastic"
    assert caps.supports_slice_readback is False
    assert caps.max_evidence_level == "derived-chunk"


def test_weknora_search_fails_loudly(weknora):
    with pytest.raises(NotImplementedProvider):
        weknora.search(EvidenceSearchRequest(scope_binding_ids=("wk-1",), query=QUERY))
