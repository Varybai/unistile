"""Turn Runtime：义务派生 + 状态机 + answer 门禁。

这一层要证明的不是"能查到证据"，而是"查不全就答不了"。
"""

from __future__ import annotations

import json

import pytest

from unistile.app import Runtime
from unistile.evidence.routing import RouteRule, Router
from unistile.turn import obligations as obl
from unistile.turn.contract import BudgetLedger, TurnError
from unistile.turn.session import TurnSession

AGR = "kn:agreement:AGR0048"
SUP = "kn:agreement:Supplement-02"
SPEC = "kn:equipment:A-1007:acceptance-spec"
EQUIP = "kn:equipment:A-1007"
AMEND = f"obl-amendments:{AGR}"
SOURCE = obl.GENERIC_SOURCE_ID

WARRANTY = "A-1007 的质保期是多久？"


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


@pytest.fixture
def ts(rt):
    return TurnSession(rt)


# ---------- 义务派生：看 Catalog 事实，不看问题措辞 ----------
def test_obligations_come_from_catalog_not_wording(rt):
    scope = rt.expand_scope([AGR])
    a = [o.id for o in obl.derive(rt, scope)]
    b = [o.id for o in obl.derive(rt, scope)]
    assert a == b == [SOURCE, AMEND]


def test_amends_edge_creates_obligation_spanning_both_endpoints(rt):
    o = next(o for o in obl.derive(rt, rt.expand_scope([AGR])) if o.id == AMEND)
    # 原条款在被改的一方，修改内容在改它的一方 —— 只读一端不算确认
    assert set(o.scope_uids) == {SUP, AGR}
    assert (o.min_evidence_count, o.min_distinct_concepts) == (2, 2)
    assert o.required and "amends" in o.derived_from


def test_clause_metadata_becomes_a_structural_requirement(rt):
    """边上登记的 clause: "7.2" 不是装饰品 —— 它变成"那一节必须在场"。"""
    o = next(o for o in obl.derive(rt, rt.expand_scope([AGR])) if o.id == AMEND)
    assert o.required_section == "7.2 质保期限"
    assert o.hint_degraded is None
    assert "第 7.2 条" in o.requirement


def test_no_amends_edge_no_amendment_obligation(rt):
    ids = [o.id for o in obl.derive(rt, [SPEC])]
    assert ids == [SOURCE]                 # 验收规范没有 amends 入边，派了就是烧预算


def test_structured_only_scope_leaves_generic_obligation_unsatisfiable(rt):
    o = obl.derive(rt, [EQUIP])[0]
    assert o.scope_uids == ()              # A-1007 是 structured，没有可回读原文


# ---------- 门禁 ----------
def test_answer_refused_before_any_evidence(ts):
    state = ts.start(WARRANTY, seeds=[AGR])
    assert "answer" not in ts.packet(state)["legal_actions"]
    with pytest.raises(TurnError, match="required_obligation_unsupported"):
        ts.answer(state, "12 个月")


def test_original_clause_alone_does_not_open_the_gate(ts):
    """核心用例：原协议写 12 个月，补充协议改成 24 个月。
    只查原文就回答会得到错的答案 —— 门禁必须拦住。"""
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.search_document_evidence(state, SOURCE, query="质保期限")
    assert state.contract.obligation(SOURCE).status == "supported"
    assert state.contract.obligation(AMEND).status == "unseen"

    with pytest.raises(TurnError) as e:
        ts.answer(state, "12 个月")
    assert AMEND in str(e.value)

    ts.search_document_evidence(state, AMEND, query="质保期限")
    out = ts.answer(state, "24 个月")
    assert out["stop_reason"] == "all_required_obligations_supported"
    assert out["unresolved_gaps"] == []


def test_answer_appears_in_legal_actions_only_when_gate_opens(ts):
    state = ts.start(WARRANTY, seeds=[AGR])
    for oid in (SOURCE, AMEND):
        assert "answer" not in ts.packet(state)["legal_actions"]
        ts.search_document_evidence(state, oid, query="质保期限")
    assert "answer" in ts.packet(state)["legal_actions"]


def test_required_obligations_cannot_be_emptied(ts):
    """Agent 若能清空义务，门禁形同虚设。清空必须炸，不是静默放行。"""
    state = ts.start(WARRANTY, seeds=[AGR])
    state.contract.obligations.clear()
    with pytest.raises(TurnError, match="门禁失效"):
        state.ledger.gate()


# ---------- scope 纪律 ----------
def test_evidence_from_wrong_concept_does_not_satisfy_obligation(ts):
    """用原协议的原文去满足"补充协议改了什么"，不算数。"""
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.search_document_evidence(state, SOURCE, query="质保期限")
    assert any(ev.concept_uid == AGR for ev in state.ledger.evidence.values())
    # 证据记在 SOURCE 名下，不会顺带把另一条义务也点亮
    assert state.contract.obligation(AMEND).status == "unseen"


def test_empty_scope_obligation_blocked_at_seed(ts):
    state = ts.start("A-1007 的额定功率是多少？", seeds=[EQUIP])
    o = state.contract.obligation(SOURCE)
    assert o.status == "blocked" and "scope 为空" in o.blocked_reason
    with pytest.raises(TurnError, match="abstain_blocked_obligation"):
        ts.answer(state, "75 kW")


# ---------- 能力门槛：换 Provider 不改一行调用代码 ----------
def test_insufficient_provider_capability_blocks_then_abstains(tmp_path, bundle):
    """把文档路由到 null Provider：天花板 provider_opaque < 要求 original-resource。
    同一条命令必须从 answer 变成 abstain。"""
    r = Runtime(bundle, tmp_path / "runtime")
    r.router = Router((RouteRule("null", evidence_class="document"),))
    r.ingest()
    ts = TurnSession(r)
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.search_document_evidence(state, SOURCE, query="质保期限")

    o = state.contract.obligation(SOURCE)
    assert o.status == "blocked" and "capability.insufficient" in o.blocked_reason
    with pytest.raises(TurnError, match="abstain_blocked_obligation"):
        ts.answer(state, "12 个月")
    out = ts.abstain(state, "无可回读原文")
    assert out["stop_reason"] == "abstain_blocked_obligation"
    r.close()


# ---------- 预算 ----------
def test_reserves_are_not_spendable(ts):
    b = BudgetLedger(context_tokens=5000, verification_reserve=1000, answer_reserve=800)
    assert b.tokens_available == 3200
    assert b.can_afford(tokens=3500)[0] is False
    assert b.can_afford(tokens=3000)[0] is True


def test_tool_call_budget_stops_further_search(ts):
    state = ts.start(WARRANTY, seeds=[AGR], budget=BudgetLedger(tool_calls=1))
    ts.search_document_evidence(state, SOURCE, query="质保期限")
    with pytest.raises(TurnError, match="tool_calls 预算耗尽"):
        ts.search_document_evidence(state, AMEND, query="质保期限")
    assert "search_document_evidence" not in ts.packet(state)["legal_actions"]


# ---------- 状态持久化 ----------
def test_turn_state_survives_a_reload(ts):
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.search_document_evidence(state, SOURCE, query="质保期限")
    again = ts.load(state.turn_id)
    assert again.contract.obligation(SOURCE).status == "supported"
    assert again.ledger.evidence.keys() == state.ledger.evidence.keys()
    ev = next(iter(again.ledger.evidence.values()))
    assert ev.evidence_level == "original-resource" and ev.locator.char_start is not None


def test_answered_turn_rejects_further_actions(ts):
    state = ts.start(WARRANTY, seeds=[AGR])
    for oid in (SOURCE, AMEND):
        ts.search_document_evidence(state, oid, query="质保期限")
    ts.answer(state, "24 个月")
    with pytest.raises(TurnError, match="已 answered"):
        ts.search_document_evidence(state, SOURCE, query="质保期限")


def test_trace_records_every_transition(ts):
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.search_document_evidence(state, SOURCE, query="质保期限")
    raw = json.loads((ts.dir / f"{state.turn_id}.json").read_text(encoding="utf-8"))
    assert raw["trace"][0]["action"] == "start"
    assert [t["to"] for t in raw["transitions"]] == ["candidate", "supported"]


# ---------- 限制性结论 vs 拒答 ----------
def test_unreachable_threshold_blocks_at_seed_without_burning_budget(ts):
    """--no-hop 后补充协议不在范围里，两来源门槛够不着 —— 开轮就 blocked，不是查到预算耗尽。"""
    state = ts.start(WARRANTY, seeds=[AGR], no_hop=True)
    o = state.contract.obligation(AMEND)
    assert o.status == "blocked" and "只够得着 1 个来源" in o.blocked_reason
    assert state.contract.budget.spent_tool_calls == 0


def test_non_critical_block_yields_qualified_answer(ts):
    state = ts.start(WARRANTY, seeds=[AGR], no_hop=True)
    ts.read_view_node(state, SOURCE, "AGR0048#6")
    out = ts.answer(state, "12 个月（未能核实补充协议）")
    assert out["stop_reason"] == "qualified_answer_with_gaps"
    assert out["unresolved_gaps"] == [AMEND]


def test_strict_answer_turns_the_same_gap_into_abstain(ts):
    state = ts.start(WARRANTY, seeds=[AGR], no_hop=True, allow_qualified_answer=False)
    ts.read_view_node(state, SOURCE, "AGR0048#6")
    with pytest.raises(TurnError, match="abstain_blocked_obligation"):
        ts.answer(state, "12 个月")


# ---------- 聚合门槛 ----------
def test_one_slice_no_longer_satisfies_the_amendment_obligation(ts):
    """收紧前：读一段便宜的原文就 supported。收紧后：条数、来源数、被点名条款缺一不可。"""
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.read_view_node(state, AMEND, "Supplement-02#1")
    o = state.contract.obligation(AMEND)
    assert o.status == "candidate"
    ok, missing = state.ledger.aggregate_check(o)
    assert not ok and "来源文档 1/2" in missing and "7.2 质保期限" in missing


def test_named_clause_must_be_present(ts):
    """两条证据、两个来源都齐了，但被点名的 7.2 不在场 —— 仍然不放行。"""
    state = ts.start(WARRANTY, seeds=[AGR])
    ts.read_view_node(state, AMEND, "Supplement-02#1")
    ts.read_view_node(state, AMEND, "AGR0048#7")          # 7.3，不是 7.2
    o = state.contract.obligation(AMEND)
    assert o.status == "candidate"
    assert "7.2 质保期限" in state.ledger.aggregate_check(o)[1]
    ts.read_view_node(state, AMEND, "AGR0048#6")          # 7.2 到场
    assert state.contract.obligation(AMEND).status == "supported"
