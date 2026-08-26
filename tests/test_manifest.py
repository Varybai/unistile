"""Local Navigation Manifest：Agent 看得见什么，以及看不见什么。

这一层要证明的是"不用编查询词也能把义务推到 supported"，
以及"没展示 ≠ 不存在" —— 省略必须显式公开。
"""

from __future__ import annotations

import pytest

from unistile.app import Runtime
from unistile.turn import manifest as mf
from unistile.turn import obligations as obl
from unistile.turn.contract import BudgetLedger, TurnError
from unistile.turn.session import TurnSession

AGR = "kn:agreement:AGR0048"
SUP = "kn:agreement:Supplement-02"
AMEND = f"obl-amendments:{AGR}"
SOURCE = obl.GENERIC_SOURCE_ID
WARRANTY = "A-1007 的质保期是多久？"

CLAUSE_72 = "AGR0048#6"        # 7.2 质保期限
AMENDMENT_2 = "Supplement-02#2"  # 2. 质保期限的变更


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


@pytest.fixture
def ts(rt):
    return TurnSession(rt)


@pytest.fixture
def state(ts):
    return ts.start(WARRANTY, seeds=[AGR])


# ---------- 根层 ----------
def test_root_layer_lists_concepts_in_scope(rt, state):
    m = mf.build(rt, state.contract)
    assert [h.view_node_id for h in m.child_handles] == ["AGR0048", "Supplement-02"]
    assert all(h.kind == "concept" for h in m.child_handles)


def test_coverage_hints_are_scope_membership_not_relevance(rt, state):
    m = mf.build(rt, state.contract)
    by_id = {h.view_node_id: h for h in m.child_handles}
    # amends 义务横跨两端（原条款在 AGR0048，修改内容在 Supplement-02）
    assert set(by_id["AGR0048"].coverage_hints) == {SOURCE, AMEND}
    assert set(by_id["Supplement-02"].coverage_hints) == {SOURCE, AMEND}
    assert m.to_dict()["child_handles"][0]["hint_basis"] == mf.HINT_BASIS


def test_satisfied_concept_collapses_into_omission(rt, ts, state):
    ts.read_view_node(state, SOURCE, CLAUSE_72)
    # SOURCE 满足了，但 AMEND 还开着，两个 Concept 都还在它范围里 —— 不折叠
    assert len(mf.build(rt, state.contract).child_handles) == 2

    ts.read_view_node(state, AMEND, CLAUSE_72)
    ts.read_view_node(state, AMEND, AMENDMENT_2)
    m = mf.build(rt, state.contract)
    assert m.child_handles == () and m.child_count == 0
    assert "已满足" in m.omission_summary and "AGR0048" in m.omission_summary


# ---------- 逐层展开 ----------
def test_expanding_a_concept_reveals_its_sections(rt, state):
    m = mf.build(rt, state.contract, node="AGR0048")
    heads = [h.head for h in m.child_handles]
    assert heads == ["1. 定义", "2. 交付", "3. 价格与付款", "7. 质量保证", "9. 争议解决"]
    assert next(h for h in m.child_handles if h.head == "7. 质量保证").child_count == 3


def test_expanding_a_section_reveals_subsections(rt, state):
    m = mf.build(rt, state.contract, node="AGR0048#4")
    assert [h.view_node_id for h in m.child_handles] == ["AGR0048#5", "AGR0048#6", "AGR0048#7"]
    c = next(h for h in m.child_handles if h.view_node_id == CLAUSE_72)
    assert c.head == "7.2 质保期限" and "12 个月" in c.preview


def test_leaf_section_has_no_next_layer(rt, state):
    with pytest.raises(mf.ManifestError, match="没有下一层"):
        mf.build(rt, state.contract, node=CLAUSE_72)


def test_unknown_view_node_id_is_rejected(rt, state):
    with pytest.raises(mf.ManifestError, match="未知 view_node_id"):
        mf.build(rt, state.contract, node="NOT-A-DOC")


# ---------- 省略必须公开 ----------
def test_pagination_exposes_total_omission_and_cursor(rt, state):
    m = mf.build(rt, state.contract, node="AGR0048", limit=2)
    assert m.child_count == 5 and len(m.child_handles) == 2
    assert m.next_cursor == 2
    assert "另有 3 项未列出" in m.omission_summary
    assert "9. 争议解决" in m.omission_summary        # 没展示的东西要点名

    page2 = mf.build(rt, state.contract, node="AGR0048", limit=2, cursor=2)
    assert page2.next_cursor == 4
    assert [h.view_node_id for h in page2.child_handles] == ["AGR0048#3", "AGR0048#4"]


def test_last_page_has_no_cursor(rt, state):
    m = mf.build(rt, state.contract, node="AGR0048", limit=2, cursor=4)
    assert m.next_cursor is None and len(m.child_handles) == 1


# ---------- read：不编查询词 ----------
def test_navigation_only_path_satisfies_every_obligation(ts, state):
    """P2 的验收：全程不出现任何自造查询词。"""
    ts.read_view_node(state, SOURCE, CLAUSE_72)
    ts.read_view_node(state, AMEND, CLAUSE_72)          # 被点名的原条款
    ts.read_view_node(state, AMEND, AMENDMENT_2)        # 修改后的内容
    out = ts.answer(state, "24 个月")
    assert out["stop_reason"] == "all_required_obligations_supported"
    assert [e["locator"]["section_path"][-1] for e in out["evidence"]] == [
        "7.2 质保期限", "7.2 质保期限", "2. 质保期限的变更",
    ]


def test_read_evidence_is_original_resource_with_exact_span(ts, state):
    ts.read_view_node(state, SOURCE, CLAUSE_72)
    ev = next(iter(state.ledger.evidence.values()))
    assert ev.evidence_level == "original-resource"
    assert ev.provider_id == "runtime:read" and ev.score_kind == "none"
    assert (ev.locator.char_start, ev.locator.char_end) == (310, 390)
    assert "12 个月" in ev.evidence_text


def test_read_outside_obligation_scope_is_refused(ts):
    """拿验收规范的章节去满足"补充协议改了什么"，不算数。"""
    state = ts.start(WARRANTY, seeds=[AGR, "kn:equipment:A-1007:acceptance-spec"])
    with pytest.raises(TurnError, match="不在 .* 的 scope"):
        ts.read_view_node(state, AMEND, "acceptance-spec#1")


def test_read_on_a_concept_node_asks_you_to_expand_first(ts, state):
    with pytest.raises(TurnError, match="先展开一层"):
        ts.read_view_node(state, SOURCE, "AGR0048")


def test_read_is_charged_by_span_size(ts, rt):
    state = ts.start(WARRANTY, seeds=[AGR], budget=BudgetLedger(context_tokens=2740))
    # 可用 = 2740 - 2700 预留 = 40；7.2 约 40 tokens 刚好，7. 质量保证 93 tokens 不行
    with pytest.raises(TurnError, match="token 预算不足"):
        ts.read_view_node(state, SOURCE, "AGR0048#4")
    ts.read_view_node(state, SOURCE, CLAUSE_72)
    assert state.contract.obligation(SOURCE).status == "supported"


def test_manifest_reaches_the_packet(ts, state):
    pk = ts.packet(state, node="AGR0048", limit=2)
    m = pk["manifest"]
    assert m["node"] == "AGR0048" and m["returned"] == 2 and m["child_count"] == 5
    assert m["next_cursor"] == 2 and m["omission_summary"]
    assert "read" in pk["legal_actions"]
