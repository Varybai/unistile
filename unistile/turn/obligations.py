"""义务派生 —— Runtime 从 Catalog 事实算出「这一轮必须查完什么」。

为什么不看问题措辞：
  「A-1007 质保多久」和「A-1007 的质保期限是多少」该查的东西完全一样，
  但任何关键词表都会漏掉其中一种。真正决定要查什么的是范围里的
  Concept 有什么事实 —— 有没有被 amends、是不是 superseded、有没有原文可回读。
  这些全在 Catalog 里，能算出来就不手写。

分层（v1 只做 L1 + L3）：
  L1  结构派生   Catalog 事实       纯规则，主力
  L2  任务形状   问题类型           留空，等 fallback 命中率上来再接
  L3  兜底       常量               保证 required 永不为 0，否则门禁形同虚设

聚合门槛：登记过的治理事实要能约束运行时，否则登记它没有意义。
  amends 边上的 clause: "7.2" 会变成"必须读到 7.2 那一节"的硬要求；
  编号体系对不上时降级为"至少读到目标文档"，不把整份文档变成不可回答。
"""

from __future__ import annotations

import json
import sqlite3

from . import manifest as mf
from .contract import EvidenceObligation

GENERIC_SOURCE_ID = "obl-original-source"

DOCUMENT_CLASSES = frozenset({"document"})


def _get(rt, uid: str) -> sqlite3.Row | None:
    return rt.catalog.get_concept(uid)


def _edge_metadata(edge: sqlite3.Row) -> dict:
    raw = dict(edge).get("metadata")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _section_for_clause(rt, concept_uid: str, clause: str) -> str | None:
    """目标文档里有没有一节的标题带这个条款号。找不到返回 None —— 降级，不阻断。"""
    try:
        outline, _, _ = mf._outline(rt, concept_uid)
    except mf.ManifestError:
        return None
    for s in outline:
        if clause in s["path"][-1]:
            return s["path"][-1]
    return None


def derive(rt, scope_uids: list[str]) -> list[EvidenceObligation]:
    """从 scope 里每个 Concept 的事实派生义务。同一 scope 反复调用结果相同。"""
    obligations: list[EvidenceObligation] = [_generic_source(rt, scope_uids)]

    for uid in scope_uids:
        row = _get(rt, uid)
        if row is None:
            continue

        # L1-1：有 amends 入边 —— 确实存在改它的文件，必须去看
        edges = rt.catalog.edges_to(uid, ("amends",))
        if edges:
            obligations.append(_amendments(rt, uid, row, edges))

        # L1-2：已被取代 —— 不确认现行版本就回答，等于拿废止条款作答
        if row["status"] == "superseded":
            superseding = [e["source_uid"] for e in rt.catalog.edges_to(uid, ("supersedes",))]
            obligations.append(
                EvidenceObligation(
                    id=f"obl-current-version:{uid}",
                    requirement=f"《{row['title']}》已被取代，须确认现行版本的对应条款",
                    minimum_evidence_level="original-resource",
                    required=True,
                    priority="critical",
                    scope_uids=tuple(superseding),
                    derived_from=f"concepts.status=superseded; supersedes <- {superseding}",
                )
            )

    return obligations


def _amendments(rt, uid: str, row: sqlite3.Row, edges) -> EvidenceObligation:
    """两端都要读：被点名的原条款 + 修改文件里的内容。只读一端不算确认。"""
    amending = [e["source_uid"] for e in edges]
    clauses = sorted({c for e in edges if (c := _edge_metadata(e).get("clause"))})

    required_section = None
    degraded = None
    if clauses:
        clause = clauses[0]
        required_section = _section_for_clause(rt, uid, clause)
        if required_section is None:
            degraded = (
                f"边元数据声明改的是第 {clause} 条，但《{row['title']}》里找不到对应 section"
                f"（编号体系不一致）；降级为「至少读到目标文档」"
            )

    clause_note = f"（第 {'、'.join(clauses)} 条）" if clauses else ""
    return EvidenceObligation(
        id=f"obl-amendments:{uid}",
        requirement=f"确认《{row['title']}》{clause_note}被 {len(amending)} 份补充文件修改的具体内容",
        minimum_evidence_level="original-resource",
        required=True,
        priority="required",
        # 原条款在 target 里，修改内容在 source 里 —— 两端都进 scope
        scope_uids=(*amending, uid),
        min_evidence_count=2,
        min_distinct_concepts=2,
        required_section=required_section,
        hint_degraded=degraded,
        derived_from=(
            f"catalog.edges_to({uid}, amends) -> {amending}"
            + (f"; edge.metadata.clause={clauses}" if clauses else "")
        ),
    )


def _generic_source(rt, scope_uids: list[str]) -> EvidenceObligation:
    """L3 兜底：至少一段可回读的原文。scope 为空时立刻 blocked，不伪装成可满足。

    它故意是弱的 —— 职责是保证 required 永不为 0，不是保证证据相关。
    相关性来自 L1 的结构约束，不来自这里。
    """
    doc_uids = tuple(
        uid for uid in scope_uids
        if (r := _get(rt, uid)) is not None and r["evidence_class"] in DOCUMENT_CLASSES
    )
    return EvidenceObligation(
        id=GENERIC_SOURCE_ID,
        requirement="至少给出一段可回读的原文，支撑结论",
        minimum_evidence_level="original-resource",
        required=True,
        priority="critical",
        scope_uids=doc_uids,
        derived_from=f"L3 兜底；scope 内 evidence_class=document 的 Concept: {list(doc_uids)}",
    )
