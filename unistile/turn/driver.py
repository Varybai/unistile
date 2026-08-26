"""RuleSelector —— 把 packet 变成动作的最笨实现。

它存在的理由不是"规则聪明"，是**验证 packet 的信息足以驱动决策**：
manifest 的 coverage_hints、字符成本、省略说明，以及 legal_actions，
到今天为止没有任何东西真的消费过。最笨的消费者能走通，说明信息够用；
走不通，就是 Runtime 少给了东西 —— 那是 Runtime 的缺陷，不是决策者的。

同时它给出可 diff 的参考轨迹：任何驾驶者跑同一个问题，
stop_reason 与它不同，只有两种可能 —— 找到了更短的路，或者绕过了门禁。

零语义判断：挑义务按优先级，挑入口按成本。它读不懂"质保期限"比"争议解决"更相关。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import manifest as mf
from .contract import EvidenceObligation, TaskContract, TurnError

PRIORITY_ORDER = {"critical": 0, "required": 1, "supporting": 2}
OPEN = ("unseen", "candidate")
MAX_DEPTH = 6


@dataclass(frozen=True)
class Step:
    n: int
    action: str
    obligation_id: str | None = None
    view_node_id: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"n": self.n, "action": self.action}
        if self.obligation_id:
            d["obligation_id"] = self.obligation_id
        if self.view_node_id:
            d["view_node_id"] = self.view_node_id
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class AutoResult:
    turn_id: str
    question: str
    stop_reason: str
    claim: str | None = None
    steps: list[Step] = field(default_factory=list)
    obligations: dict[str, str] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "question": self.question,
            "stop_reason": self.stop_reason,
            "claim": self.claim,
            "obligations": self.obligations,
            "reads": self.reads,
            "steps": [s.to_dict() for s in self.steps],
        }


def _open_obligations(contract: TaskContract) -> list[EvidenceObligation]:
    return sorted(
        (o for o in contract.obligations if o.required and o.status in OPEN),
        key=lambda o: (PRIORITY_ORDER.get(o.priority, 9), contract.obligations.index(o)),
    )


def _leaves(rt, contract: TaskContract, obligation_id: str) -> list[mf.ChildHandle]:
    """从根层逐层展开，收集覆盖该义务的可读叶子。展开只读本地导航数据，不花预算。"""
    out: list[mf.ChildHandle] = []
    frontier: list[tuple[str | None, int]] = [(None, 0)]
    seen: set[str] = set()
    while frontier:
        node, depth = frontier.pop(0)
        if depth > MAX_DEPTH:
            continue
        try:
            m = mf.build(rt, contract, node=node, limit=1000)
        except mf.ManifestError:
            continue
        for h in m.child_handles:
            if obligation_id not in h.coverage_hints or h.view_node_id in seen:
                continue
            seen.add(h.view_node_id)
            if h.child_count:
                frontier.append((h.view_node_id, depth + 1))
            elif h.kind == "section":
                out.append(h)
    return out


def run(session, state, *, max_steps: int = 20, claim: str | None = None) -> AutoResult:
    rt = session.rt
    contract = state.contract
    result = AutoResult(turn_id=state.turn_id, question=contract.question, stop_reason="")
    visited: set[tuple[str, str]] = set()
    n = 0

    while n < max_steps:
        n += 1
        decision = state.ledger.gate()
        if decision.allowed:
            text = claim or f"（RuleSelector 无结论，仅验证控制流；证据见 {len(state.ledger.evidence)} 条）"
            session.answer(state, text)
            result.claim = text
            result.stop_reason = decision.stop_reason
            result.steps.append(Step(n, "answer", note=decision.stop_reason))
            break

        pending = _open_obligations(contract)
        if not pending:
            session.abstain(state, "所有 required 义务均 blocked")
            result.stop_reason = "abstain_blocked_obligation"
            result.steps.append(Step(n, "abstain", note="; ".join(decision.blocked)))
            break

        o = pending[0]
        candidates = [
            h for h in _leaves(rt, contract, o.id)
            if (o.id, h.view_node_id) not in visited
        ]
        if not candidates:
            state.ledger._move(o, "blocked", "已无未读的导航入口可覆盖本条")
            result.steps.append(Step(n, "exhausted", o.id, note="没有可读入口"))
            session.save(state)
            continue

        # 只按还没满足的那部分门槛排序，满足了的不再牵引
        _, missing = state.ledger.aggregate_check(o)
        have = {state.ledger.evidence[i].concept_uid for i in o.evidence_ids}
        need_section = o.required_section if "被点名条款" in missing else None
        need_new_concept = "来源文档" in missing
        candidates.sort(
            key=lambda h: (
                0 if need_section and need_section in " / ".join(h.section_path) else 1,
                0 if need_new_concept and h.concept_uid not in have else 1,
                h.expected_tokens,
                h.view_node_id,
            )
        )
        pick = next(
            (h for h in candidates
             if contract.budget.can_afford(tokens=h.expected_tokens, tool_calls=1, evidence_reads=1)[0]),
            None,
        )
        if pick is None:
            session.abstain(state, "剩余预算读不下任何入口")
            result.stop_reason = "budget_exhausted"
            result.steps.append(Step(n, "abstain", o.id, note="budget_exhausted"))
            break

        visited.add((o.id, pick.view_node_id))
        session.read_view_node(state, o.id, pick.view_node_id)
        result.reads.append(pick.view_node_id)
        result.steps.append(
            Step(n, "read", o.id, pick.view_node_id, f"~{pick.expected_tokens} tokens")
        )
    else:
        session.abstain(state, f"超过 {max_steps} 步仍未收敛")
        result.stop_reason = "no_legal_action"
        result.steps.append(Step(n, "abstain", note=f"max_steps={max_steps}"))

    result.obligations = {o.id: o.status for o in contract.obligations}
    return result
