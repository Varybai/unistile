"""TaskContract / EvidenceObligation / BudgetLedger —— 一轮问答的完成条件。

三条硬规则：
  1. 义务由 Runtime 从 Catalog 事实派生，外部 Agent 不能删除 required 条目。
     否则它可以写出 obligations=[] 让门禁自动通过。
  2. 每条义务带 scope_uids —— 只有来自这些 Concept 的证据才算数，
     防止用甲文件的原文去满足乙文件的义务。
  3. 预算的 verification/answer 预留在 can_afford 时就扣掉，
     不能等到最后才检查 —— 那时候已经被检索花光了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..evidence.contract import EvidenceLevel

CONTRACT_VERSION = "unistile/turn-contract/v1"

ObligationStatus = Literal["unseen", "candidate", "supported", "blocked"]
ObligationPriority = Literal["critical", "required", "supporting"]

ActionType = Literal[
    "inspect", "expand", "search_document_evidence", "expand_document_context",
    "read", "follow", "switch_view", "compare", "refresh", "verify",
    "compress", "backtrack", "answer", "abstain",
]

StopReason = Literal[
    "all_required_obligations_supported",
    "qualified_answer_with_gaps",
    "required_obligation_unsupported",
    "abstain_blocked_obligation",
    "budget_exhausted",
    "no_legal_action",
]

LEVEL_ORDER: dict[EvidenceLevel, int] = {
    "provider_opaque": 0,
    "derived-chunk": 1,
    "original-resource": 2,
}


class TurnError(RuntimeError):
    """轮次层错误：门禁拒绝、预算耗尽、非法动作。"""


@dataclass
class EvidenceObligation:
    """回答前必须满足的可检查条件。status 由确定性 Verifier 改写，不由模型自报。"""

    id: str
    requirement: str
    minimum_evidence_level: EvidenceLevel = "original-resource"
    required: bool = True
    priority: ObligationPriority = "required"
    scope_uids: tuple[str, ...] = ()      # 只有这些 Concept 的证据能满足本条
    derived_from: str = ""                # 派生依据，写进 trace 供审计
    # 聚合门槛：一条便宜的原文不该满足"确认修改了什么"
    min_evidence_count: int = 1
    min_distinct_concepts: int = 1
    required_section: str | None = None   # 来自边元数据的 clause，如 "7.2"
    hint_degraded: str | None = None      # clause 在目标文档里找不到对应 section 时降级的原因
    status: ObligationStatus = "unseen"
    evidence_ids: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "requirement": self.requirement,
            "minimum_evidence_level": self.minimum_evidence_level,
            "required": self.required,
            "priority": self.priority,
            "scope_uids": list(self.scope_uids),
            "derived_from": self.derived_from,
            "min_evidence_count": self.min_evidence_count,
            "min_distinct_concepts": self.min_distinct_concepts,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.required_section:
            d["required_section"] = self.required_section
        if self.hint_degraded:
            d["hint_degraded"] = self.hint_degraded
        if self.blocked_reason:
            d["blocked_reason"] = self.blocked_reason
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceObligation:
        return cls(
            id=d["id"],
            requirement=d["requirement"],
            minimum_evidence_level=d.get("minimum_evidence_level", "original-resource"),
            required=d.get("required", True),
            priority=d.get("priority", "required"),
            scope_uids=tuple(d.get("scope_uids", ())),
            derived_from=d.get("derived_from", ""),
            min_evidence_count=d.get("min_evidence_count", 1),
            min_distinct_concepts=d.get("min_distinct_concepts", 1),
            required_section=d.get("required_section"),
            hint_degraded=d.get("hint_degraded"),
            status=d.get("status", "unseen"),
            evidence_ids=list(d.get("evidence_ids", [])),
            blocked_reason=d.get("blocked_reason"),
        )


@dataclass
class BudgetLedger:
    """多维预算。预留部分对普通动作不可见，只留给 verify 和 answer。"""

    tool_calls: int = 8
    evidence_reads: int = 5
    context_tokens: int = 12000
    verification_reserve: int = 1500
    answer_reserve: int = 1200
    spent_tool_calls: int = 0
    spent_evidence_reads: int = 0
    spent_tokens: int = 0

    @property
    def reserved(self) -> int:
        return self.verification_reserve + self.answer_reserve

    @property
    def tokens_available(self) -> int:
        """普通动作能花的 token —— 已经扣掉预留。"""
        return max(0, self.context_tokens - self.spent_tokens - self.reserved)

    @property
    def exhausted(self) -> bool:
        return (
            self.spent_tool_calls >= self.tool_calls
            or self.spent_evidence_reads >= self.evidence_reads
            or self.tokens_available <= 0
        )

    def can_afford(self, *, tokens: int = 0, tool_calls: int = 1, evidence_reads: int = 0) -> tuple[bool, str]:
        if self.spent_tool_calls + tool_calls > self.tool_calls:
            return False, f"tool_calls 预算耗尽（{self.spent_tool_calls}/{self.tool_calls}）"
        if self.spent_evidence_reads + evidence_reads > self.evidence_reads:
            return False, f"evidence_reads 预算耗尽（{self.spent_evidence_reads}/{self.evidence_reads}）"
        if tokens > self.tokens_available:
            return False, (
                f"token 预算不足：需 {tokens}，可用 {self.tokens_available}"
                f"（已留 {self.reserved} 给 verify/answer）"
            )
        return True, ""

    def charge(self, *, tokens: int = 0, tool_calls: int = 1, evidence_reads: int = 0) -> None:
        self.spent_tool_calls += tool_calls
        self.spent_evidence_reads += evidence_reads
        self.spent_tokens += tokens

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BudgetLedger:
        return cls(**d)


@dataclass
class TaskContract:
    """自然语言问题 → 可执行的完成条件。"""

    task_id: str
    question: str
    seed_uids: list[str]
    scope_uids: list[str]
    as_of: str
    obligations: list[EvidenceObligation]
    budget: BudgetLedger
    risk: str = "medium"
    require_citations: bool = True
    allow_qualified_answer: bool = True

    def obligation(self, oid: str) -> EvidenceObligation:
        for o in self.obligations:
            if o.id == oid:
                return o
        raise TurnError(f"未知 obligation：{oid}（本轮只有 {[o.id for o in self.obligations]}）")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "task_id": self.task_id,
            "question": self.question,
            "seed_uids": list(self.seed_uids),
            "scope_uids": list(self.scope_uids),
            "as_of": self.as_of,
            "risk": self.risk,
            "require_citations": self.require_citations,
            "allow_qualified_answer": self.allow_qualified_answer,
            "obligations": [o.to_dict() for o in self.obligations],
            "budget": self.budget.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskContract:
        return cls(
            task_id=d["task_id"],
            question=d["question"],
            seed_uids=list(d["seed_uids"]),
            scope_uids=list(d["scope_uids"]),
            as_of=d["as_of"],
            obligations=[EvidenceObligation.from_dict(o) for o in d["obligations"]],
            budget=BudgetLedger.from_dict(d["budget"]),
            risk=d.get("risk", "medium"),
            require_citations=d.get("require_citations", True),
            allow_qualified_answer=d.get("allow_qualified_answer", True),
        )
