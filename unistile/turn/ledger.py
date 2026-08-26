"""ObligationLedger —— 状态机 + answer 门禁。

状态只有四个，转换由确定性代码执行，不由模型自报：

    unseen ──Provider 返回候选──> candidate
    candidate ──scope/等级/回读校验通过──> supported
    candidate ──能力不足/资源不可读/scope 为空──> blocked
    supported ──发现冲突或版本变化──> candidate

Provider 的排序分数只影响候选顺序，永远不能直接把义务改成 supported。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..evidence.adapter import Evidence, RejectedCandidate
from ..evidence.contract import EvidenceLevel
from .contract import (
    LEVEL_ORDER,
    ActionType,
    EvidenceObligation,
    ObligationStatus,
    StopReason,
    TaskContract,
    TurnError,
)

# 哪些拒绝码换个查法还有救，哪些是这条义务走不通了
RETRYABLE_REJECTS = frozenset({"binding.stale"})


@dataclass(frozen=True)
class Transition:
    obligation_id: str
    frm: ObligationStatus
    to: ObligationStatus
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"obligation_id": self.obligation_id, "from": self.frm, "to": self.to, "reason": self.reason}


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    stop_reason: StopReason
    unsupported: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "stop_reason": self.stop_reason,
            "unsupported": list(self.unsupported),
            "blocked": list(self.blocked),
        }


@dataclass
class ObligationLedger:
    contract: TaskContract
    evidence: dict[str, Evidence] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)

    # ---------- 转换 ----------
    def _move(self, o: EvidenceObligation, to: ObligationStatus, reason: str) -> Transition:
        t = Transition(o.id, o.status, to, reason)
        o.status = to
        o.blocked_reason = reason if to == "blocked" else None
        self.transitions.append(t)
        return t

    def seed(self) -> list[Transition]:
        """开轮时先把不可能满足的义务打成 blocked —— 让 Agent 立刻看见死路，而不是查半天才发现。"""
        out = []
        turn_scope = set(self.contract.scope_uids)
        for o in self.contract.obligations:
            if o.status != "unseen":
                continue
            if not o.scope_uids:
                out.append(self._move(o, "blocked", "scope 为空：范围内没有能满足本条的 Concept"))
                continue
            # 门槛要的来源数超过本轮范围里够得着的数量 —— 查到底也满足不了，不要烧预算
            reachable = set(o.scope_uids) & turn_scope
            if len(reachable) < o.min_distinct_concepts:
                out.append(self._move(
                    o, "blocked",
                    f"本轮范围内只够得着 {len(reachable)} 个来源"
                    f"（{sorted(reachable)}），门槛要求 {o.min_distinct_concepts} 个",
                ))
        return out

    def preflight(self, oid: str, *, provider_max_level: EvidenceLevel, provider_id: str) -> Transition | None:
        """能力门槛检查 —— Provider 的天花板低于任务要求时，查一万次也没用。"""
        o = self.contract.obligation(oid)
        if o.status == "blocked":
            return None
        if LEVEL_ORDER[provider_max_level] < LEVEL_ORDER[o.minimum_evidence_level]:
            return self._move(
                o, "blocked",
                f"capability.insufficient：{provider_id} 最高只能给到 {provider_max_level}，"
                f"本条要求 {o.minimum_evidence_level}",
            )
        return None

    def apply(
        self,
        oid: str,
        evidence: Iterable[Evidence],
        rejected: Iterable[RejectedCandidate] = (),
    ) -> list[Transition]:
        """把一次检索的结果计入某条义务。返回状态变化。"""
        o = self.contract.obligation(oid)
        evidence = list(evidence)
        rejected = list(rejected)
        out: list[Transition] = []

        if not evidence:
            # 全被拒且没有一条是换个查法能救的 —— 这条路走死了
            if rejected and all(r.reason_code not in RETRYABLE_REJECTS for r in rejected):
                codes = sorted({r.reason_code for r in rejected})
                out.append(self._move(o, "blocked", f"候选全部被拒：{', '.join(codes)}"))
            # 否则保持原状：没查到不等于查不到
            return out

        if o.status == "unseen":
            out.append(self._move(o, "candidate", f"Provider 返回 {len(evidence)} 条候选"))

        accepted = [e for e in evidence if self._admissible(o, e)]
        if not accepted:
            reasons = sorted({self._why_not(o, e) for e in evidence})
            out.append(self._move(o, "candidate", f"候选未达标：{'; '.join(reasons)}"))
            return out

        for e in accepted:
            eid = f"ev-{len(self.evidence) + 1}"
            self.evidence[eid] = e
            o.evidence_ids.append(eid)
        added = ", ".join(o.evidence_ids[-len(accepted):])

        ok, missing = self.aggregate_check(o)
        if ok:
            out.append(self._move(o, "supported", f"{len(accepted)} 条证据入账（{added}）；聚合门槛已满足"))
        else:
            out.append(self._move(o, "candidate", f"{len(accepted)} 条证据入账（{added}）；仍缺：{missing}"))
        return out

    def invalidate(self, oid: str, reason: str) -> Transition:
        """发现冲突或版本变化：supported 退回 candidate，不是直接删。"""
        o = self.contract.obligation(oid)
        if o.status != "supported":
            raise TurnError(f"{oid} 当前是 {o.status}，只有 supported 能被 invalidate")
        return self._move(o, "candidate", reason)

    # ---------- 校验 ----------
    def _admissible(self, o: EvidenceObligation, e: Evidence) -> bool:
        """单条证据能不能入账。入账 != 满足 —— 聚合门槛另算。"""
        return (
            e.concept_uid in o.scope_uids
            and LEVEL_ORDER[e.evidence_level] >= LEVEL_ORDER[o.minimum_evidence_level]
        )

    def aggregate_check(self, o: EvidenceObligation) -> tuple[bool, str]:
        """聚合门槛：条数、来源文档数、被点名条款是否在场。全是集合运算，不判断语义。"""
        evs = [self.evidence[i] for i in o.evidence_ids]
        missing: list[str] = []
        if len(evs) < o.min_evidence_count:
            missing.append(f"证据 {len(evs)}/{o.min_evidence_count} 条")
        concepts = {e.concept_uid for e in evs}
        if len(concepts) < o.min_distinct_concepts:
            missing.append(f"来源文档 {len(concepts)}/{o.min_distinct_concepts} 个")
        if o.required_section and not any(
            o.required_section in " / ".join(e.locator.section_path) for e in evs
        ):
            missing.append(f"被点名条款「{o.required_section}」的原文未在场")
        return (not missing), "；".join(missing)

    def _why_not(self, o: EvidenceObligation, e: Evidence) -> str:
        if e.concept_uid not in o.scope_uids:
            return f"{e.concept_uid} 不在本条 scope 内"
        return f"证据等级 {e.evidence_level} < 要求 {o.minimum_evidence_level}"

    # ---------- 门禁 ----------
    def gate(self) -> GateDecision:
        required = [o for o in self.contract.obligations if o.required]
        if not required:
            raise TurnError("required 义务为 0 —— 门禁失效。义务必须由 Runtime 派生，不能被清空。")

        unsupported = tuple(o.id for o in required if o.status in ("unseen", "candidate"))
        blocked = tuple(o.id for o in required if o.status == "blocked")
        critical_blocked = tuple(
            o.id for o in required if o.status == "blocked" and o.priority == "critical"
        )

        if not unsupported and not blocked:
            return GateDecision(True, "all_required_obligations_supported")
        if critical_blocked:
            return GateDecision(False, "abstain_blocked_obligation", unsupported, blocked)
        if unsupported:
            return GateDecision(False, "required_obligation_unsupported", unsupported, blocked)
        if self.contract.allow_qualified_answer:
            return GateDecision(True, "qualified_answer_with_gaps", unsupported, blocked)
        return GateDecision(False, "abstain_blocked_obligation", unsupported, blocked)

    def legal_actions(self) -> list[ActionType]:
        """Agent 只能从这里挑。answer 不在列表里，就是物理上答不了。"""
        actions: list[ActionType] = ["inspect", "abstain"]
        decision = self.gate()
        if decision.allowed:
            actions.append("answer")

        open_ = [o for o in self.contract.obligations if o.status in ("unseen", "candidate")]
        if open_ and not self.contract.budget.exhausted:
            actions += ["expand", "read", "search_document_evidence", "follow"]
        if any(o.status == "candidate" for o in self.contract.obligations):
            actions.append("verify")
        if any(
            o.status == "blocked" and o.blocked_reason and "stale" in o.blocked_reason
            for o in self.contract.obligations
        ):
            actions.append("refresh")
        return actions
