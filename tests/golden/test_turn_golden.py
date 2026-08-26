"""整轮控制流的对照基准。

单步测试断言"这一步做对了"，这里断言"整轮的停止理由和读取轨迹稳定"。
义务派生规则、manifest 折叠、预算预留任何一处改动，单步可能全绿，这里会红。

RuleSelector 不做语义判断 —— 它按成本挑入口。因此本文件断言的是控制流，
不是答案质量；semantic_gap 字段记录那些控制流通过但证据不含答案的用例。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unistile.app import Runtime
from unistile.turn.contract import BudgetLedger
from unistile.turn.driver import run as auto_run
from unistile.turn.session import TurnSession

GOLDEN = json.loads((Path(__file__).parent / "turn_set.json").read_text(encoding="utf-8"))


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


def _run(rt, case):
    ts = TurnSession(rt)
    state = ts.start(
        case["question"],
        seeds=case["seeds"],
        no_hop=case.get("no_hop", False),
        budget=BudgetLedger(**case["budget"]) if case.get("budget") else None,
    )
    return auto_run(ts, state)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["id"])
def test_turn_trace_is_stable(rt, case):
    res = _run(rt, case)
    exp = case["expect"]
    assert res.stop_reason == exp["stop_reason"], f"{case['id']} 停止理由变了"
    assert res.reads == exp["reads"], f"{case['id']} 读取轨迹变了"
    assert res.obligations == exp["obligations"], f"{case['id']} 义务终态变了"


def test_every_answered_turn_carries_readable_evidence(rt):
    """凡是过了门禁的轮次，每条证据都必须能按 locator 原样回读。"""
    from unistile.resources.normalizer import text_sha256

    for case in GOLDEN["cases"]:
        if not case["expect"]["stop_reason"].startswith(("all_required", "qualified")):
            continue
        ts = TurnSession(rt)
        state = ts.start(case["question"], seeds=case["seeds"], no_hop=case.get("no_hop", False))
        auto_run(ts, state)
        assert state.ledger.evidence, case["id"]
        for ev in state.ledger.evidence.values():
            got = rt.adapter.fetch_slice(ev.locator)
            assert text_sha256(got) == ev.locator.content_sha256, case["id"]


def test_report_semantic_gap(rt, capsys):
    """控制流通过 ≠ 答案正确。把差距摊开，不藏在绿色里。"""
    gaps = [c for c in GOLDEN["cases"] if "semantic_gap" in c]
    with capsys.disabled():
        print(f"\n  golden(turn)  {len(GOLDEN['cases'])} 条轨迹稳定"
              f"   语义缺口 {len(gaps)}/{len(GOLDEN['cases'])}")
        for c in gaps:
            res = _run(rt, c)
            print(f"    · {c['id']} 读到 {res.reads} → {res.stop_reason}")
            print(f"      {c['semantic_gap']}")
    assert gaps, "语义缺口消失了 —— 说明义务变强了，更新 golden"
