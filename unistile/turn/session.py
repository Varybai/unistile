"""TurnSession —— 一轮问答的状态存取与合法动作执行。

Runtime 不驱动循环：start / act / answer 是三次独立调用，
中间的决策由外部 Agent 做。Runtime 只在每次调用时更新义务状态并守门。

状态落在 runtime/turns/<turn_id>.json —— 派生物，删掉不影响 knowledge/。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..envelope import evidence_to_envelope
from ..evidence.adapter import Evidence, RejectedCandidate
from ..evidence.contract import EvidenceLevel, Locator, SearchBudget
from ..evidence.errors import ProviderError, ScopeError
from ..resources.normalizer import text_sha256
from . import manifest as mf
from .contract import LEVEL_ORDER, BudgetLedger, TaskContract, TurnError
from .ledger import ObligationLedger
from .obligations import derive

READ_PROVIDER_ID = "runtime:read"   # 不是检索，是 Agent 指定区间的精确回读

TURN_ID_RE = re.compile(r"^t-\d{3,}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _evidence_to_dict(e: Evidence) -> dict[str, Any]:
    d = {k: v for k, v in e.__dict__.items() if k != "locator"}
    loc = dict(e.locator.__dict__)
    loc["section_path"] = list(loc["section_path"])
    d["locator"] = loc
    return d


def _evidence_from_dict(d: dict[str, Any]) -> Evidence:
    loc = dict(d["locator"])
    loc["section_path"] = tuple(loc["section_path"])
    return Evidence(**{**d, "locator": Locator(**loc)})


@dataclass
class TurnState:
    turn_id: str
    contract: TaskContract
    ledger: ObligationLedger
    status: str = "open"                       # open / answered / abstained
    stop_reason: str | None = None
    answer: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "created_at": self.created_at,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "contract": self.contract.to_dict(),
            "evidence": {k: _evidence_to_dict(v) for k, v in self.ledger.evidence.items()},
            "transitions": [t.to_dict() for t in self.ledger.transitions],
            "trace": self.trace,
            "answer": self.answer,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TurnState:
        contract = TaskContract.from_dict(d["contract"])
        ledger = ObligationLedger(
            contract=contract,
            evidence={k: _evidence_from_dict(v) for k, v in d.get("evidence", {}).items()},
        )
        return cls(
            turn_id=d["turn_id"],
            contract=contract,
            ledger=ledger,
            status=d.get("status", "open"),
            stop_reason=d.get("stop_reason"),
            answer=d.get("answer"),
            trace=list(d.get("trace", [])),
            created_at=d.get("created_at", _now()),
        )


class TurnSession:
    def __init__(self, runtime, turns_dir: str | Path | None = None):
        self.rt = runtime
        self.dir = Path(turns_dir) if turns_dir else Path(runtime.runtime_dir) / "turns"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------- 存取 ----------
    def _path(self, turn_id: str) -> Path:
        if not TURN_ID_RE.match(turn_id):
            raise TurnError(f"非法 turn_id：{turn_id}")
        return self.dir / f"{turn_id}.json"

    def _next_id(self) -> str:
        used = [int(p.stem[2:]) for p in self.dir.glob("t-*.json") if p.stem[2:].isdigit()]
        return f"t-{max(used, default=0) + 1:03d}"

    def save(self, state: TurnState) -> Path:
        p = self._path(state.turn_id)
        p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def load(self, turn_id: str) -> TurnState:
        p = self._path(turn_id)
        if not p.exists():
            raise TurnError(f"轮次不存在：{turn_id}（在 {self.dir}）")
        return TurnState.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("t-*.json"))

    # ---------- 开轮 ----------
    def start(
        self,
        question: str,
        *,
        seeds: Sequence[str] | None = None,
        no_hop: bool = False,
        budget: BudgetLedger | None = None,
        as_of: str | None = None,
        allow_qualified_answer: bool = True,
    ) -> TurnState:
        seed_uids = list(seeds or [])
        if not seed_uids:
            seed_uids = [r["uid"] for r in self.rt.catalog.resolve(question)][:3]
        if not seed_uids:
            raise TurnError(
                "无法定位 Concept；请用 --concept <uid> 指定范围（不允许全库无界检索）"
            )
        scope = seed_uids if no_hop else self.rt.expand_scope(seed_uids)

        turn_id = self._next_id()
        contract = TaskContract(
            task_id=turn_id,
            question=question,
            seed_uids=seed_uids,
            scope_uids=scope,
            as_of=as_of or date.today().isoformat(),
            obligations=derive(self.rt, scope),
            budget=budget or BudgetLedger(),
            allow_qualified_answer=allow_qualified_answer,
        )
        state = TurnState(turn_id=turn_id, contract=contract, ledger=ObligationLedger(contract))
        seeded = state.ledger.seed()
        state.trace.append(
            {
                "at": _now(),
                "action": "start",
                "seed_uids": seed_uids,
                "scope_uids": scope,
                "derived": [o.id for o in contract.obligations],
                "transitions": [t.to_dict() for t in seeded],
            }
        )
        self.save(state)
        return state

    # ---------- 动作：文档证据检索 ----------
    def search_document_evidence(
        self,
        state: TurnState,
        obligation_id: str,
        *,
        query: str | None = None,
        limit: int = 5,
        section_prefix: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        self._require_open(state)
        o = state.contract.obligation(obligation_id)
        if o.status == "blocked":
            raise TurnError(f"{obligation_id} 已 blocked：{o.blocked_reason}")
        if not o.scope_uids:
            raise TurnError(f"{obligation_id} 的 scope 为空，无法检索")

        ok, why = state.contract.budget.can_afford(tool_calls=1, evidence_reads=1)
        if not ok:
            raise TurnError(why)

        # 能力门槛先查：Provider 天花板低于任务要求时，查一万次也没用
        level, pid = self._scope_capability(o.scope_uids)
        pre = state.ledger.preflight(obligation_id, provider_max_level=level, provider_id=pid)
        if pre is not None:
            state.contract.budget.charge(tool_calls=1)
            self._trace(state, "search_document_evidence", obligation_id, [pre], note="preflight 拦截")
            self.save(state)
            return self.packet(state)

        try:
            bundle = self.rt.adapter.search(
                concept_uids=list(o.scope_uids),
                query=query or state.contract.question,
                obligation_ids=(obligation_id,),
                budget=SearchBudget(max_candidates=limit),
                section_prefix=section_prefix,
            )
        except ProviderError as e:
            t = state.ledger._move(o, "blocked", f"{type(e).__name__}: {e}")
            state.contract.budget.charge(tool_calls=1)
            self._trace(state, "search_document_evidence", obligation_id, [t], note="Provider 错误")
            self.save(state)
            return self.packet(state)

        transitions = state.ledger.apply(obligation_id, bundle.evidence, bundle.rejected)
        state.contract.budget.charge(
            tool_calls=1,
            evidence_reads=1 if bundle.evidence else 0,
            tokens=sum(len(e.evidence_text) for e in bundle.evidence) // 2,
        )
        self._trace(
            state, "search_document_evidence", obligation_id, transitions,
            note=f"evidence={len(bundle.evidence)} rejected={len(bundle.rejected)}",
            rejected=[r.__dict__ for r in bundle.rejected],
            omissions=bundle.omissions,
        )
        self.save(state)
        return self.packet(state)

    # ---------- 动作：按导航入口精确读取（不检索，不需要编查询词） ----------
    def read_view_node(self, state: TurnState, obligation_id: str, view_node_id: str) -> dict[str, Any]:
        self._require_open(state)
        o = state.contract.obligation(obligation_id)
        if o.status == "blocked":
            raise TurnError(f"{obligation_id} 已 blocked：{o.blocked_reason}")

        handle = mf.handle_of(self.rt, state.contract, view_node_id)
        if handle.kind != "section":
            raise TurnError(
                f"{view_node_id} 是 Concept 节点，先展开一层："
                f"unistile turn show {state.turn_id} --node {view_node_id}"
            )
        if handle.concept_uid not in o.scope_uids:
            raise TurnError(
                f"{view_node_id} 属于 {handle.concept_uid}，不在 {obligation_id} 的 scope "
                f"{list(o.scope_uids)} 内 —— 拿甲文件的原文满足不了乙文件的义务"
            )

        ok, why = state.contract.budget.can_afford(
            tokens=handle.expected_tokens, tool_calls=1, evidence_reads=1
        )
        if not ok:
            raise TurnError(why)

        span = mf.read_span(self.rt, handle.concept_uid, handle.char_start, handle.char_end)
        evidence = Evidence(
            concept_uid=handle.concept_uid,
            resource_uri=span["resource_uri"],
            resource_revision=span["resource_revision"],
            source_sha256=span["source_sha256"],
            locator=Locator(
                kind="char_span",
                resource_uri=span["resource_uri"],
                resource_revision=span["resource_revision"],
                normalized_text_sha256=span["normalized_text_sha256"],
                extractor_version=span["extractor_version"],
                char_start=handle.char_start,
                char_end=handle.char_end,
                section_path=handle.section_path,
                content_sha256=text_sha256(span["text"]),
            ),
            evidence_text=span["text"],
            evidence_level="original-resource",
            provider_id=READ_PROVIDER_ID,
            provider_version=span["extractor_version"],
            score=1.0,
            score_kind="none",          # Agent 指定的区间，没有排序分数可言
        )
        transitions = state.ledger.apply(obligation_id, [evidence])
        state.contract.budget.charge(
            tokens=handle.expected_tokens, tool_calls=1, evidence_reads=1
        )
        self._trace(
            state, "read", obligation_id, transitions,
            view_node_id=view_node_id, section_path=list(handle.section_path),
            char_span=[handle.char_start, handle.char_end],
        )
        self.save(state)
        return self.packet(state)

    # ---------- 出口：answer / abstain ----------
    def answer(self, state: TurnState, claim: str) -> dict[str, Any]:
        self._require_open(state)
        decision = state.ledger.gate()
        if not decision.allowed:
            state.trace.append(
                {"at": _now(), "action": "answer", "refused": decision.to_dict(), "claim": claim}
            )
            self.save(state)
            raise TurnError(
                f"门禁拒绝（{decision.stop_reason}）：\n"
                + "\n".join(
                    f"  {oid}  {state.contract.obligation(oid).status}"
                    f"  —— {state.contract.obligation(oid).requirement}"
                    + (f"  [{state.contract.obligation(oid).blocked_reason}]"
                       if state.contract.obligation(oid).blocked_reason else "")
                    for oid in (*decision.unsupported, *decision.blocked)
                )
            )

        state.status = "answered"
        state.stop_reason = decision.stop_reason
        state.answer = {
            "claim": claim,
            "question": state.contract.question,
            "scope": state.contract.scope_uids,
            "as_of": state.contract.as_of,
            "stop_reason": decision.stop_reason,
            "obligations": [o.to_dict() for o in state.contract.obligations],
            "evidence": [
                {"evidence_id": eid, **evidence_to_envelope(ev, claim=claim)}
                for eid, ev in state.ledger.evidence.items()
            ],
            "unresolved_gaps": list(decision.blocked),
            "trace_id": state.turn_id,
        }
        state.trace.append({"at": _now(), "action": "answer", "gate": decision.to_dict()})
        self.save(state)
        return state.answer

    def abstain(self, state: TurnState, reason: str = "") -> dict[str, Any]:
        self._require_open(state)
        decision = state.ledger.gate()
        state.status = "abstained"
        state.stop_reason = "abstain_blocked_obligation"
        state.answer = {
            "claim": None,
            "question": state.contract.question,
            "stop_reason": state.stop_reason,
            "reason": reason,
            "obligations": [o.to_dict() for o in state.contract.obligations],
            "unresolved_gaps": [*decision.unsupported, *decision.blocked],
            "trace_id": state.turn_id,
        }
        state.trace.append({"at": _now(), "action": "abstain", "reason": reason})
        self.save(state)
        return state.answer

    # ---------- 给 Agent 看的投影 ----------
    def packet(
        self, state: TurnState, *, node: str | None = None, limit: int = 8, cursor: int = 0
    ) -> dict[str, Any]:
        b = state.contract.budget
        decision = state.ledger.gate()
        return {
            "turn_id": state.turn_id,
            "status": state.status,
            "question": state.contract.question,
            "scope_uids": state.contract.scope_uids,
            "obligations": [o.to_dict() for o in state.contract.obligations],
            "gate": decision.to_dict(),
            "legal_actions": state.ledger.legal_actions(),
            "manifest": mf.build(
                self.rt, state.contract, node=node, limit=limit, cursor=cursor
            ).to_dict(),
            "budget": {
                "tool_calls": f"{b.spent_tool_calls}/{b.tool_calls}",
                "evidence_reads": f"{b.spent_evidence_reads}/{b.evidence_reads}",
                "tokens_available": b.tokens_available,
                "reserved": b.reserved,
            },
        }

    # ---------- 内部 ----------
    def _require_open(self, state: TurnState) -> None:
        if state.status != "open":
            raise TurnError(f"{state.turn_id} 已 {state.status}，不能再执行动作")

    def _scope_capability(self, uids: Sequence[str]) -> tuple[EvidenceLevel, str]:
        """scope 内所有 Binding 的 Provider 中，能力最高的那个决定这条义务的上限。"""
        bindings = self.rt.catalog.bindings_for(list(uids))
        if not bindings:
            raise ScopeError(f"{list(uids)} 没有任何 Binding，无法检索")
        best: tuple[EvidenceLevel, str] = ("provider_opaque", "-")
        for pid in sorted({b.provider_id for b in bindings}):
            caps = self.rt.registry.capabilities(pid)
            if LEVEL_ORDER[caps.max_evidence_level] > LEVEL_ORDER[best[0]]:
                best = (caps.max_evidence_level, pid)
        return best

    def _trace(self, state: TurnState, action: str, oid: str, transitions, **extra: Any) -> None:
        state.trace.append(
            {
                "at": _now(),
                "action": action,
                "obligation_id": oid,
                "transitions": [t.to_dict() for t in transitions],
                **extra,
            }
        )
