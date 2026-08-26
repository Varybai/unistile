"""Evidence Envelope：所有下游回答统一使用的证据封装。

verified_at=null 表示只是检索到的证据，尚未经过额外事实核验。
provider 私有标识只放 debug 区，不进稳定字段。
"""

from __future__ import annotations

from typing import Any

from .evidence.adapter import Evidence, EvidenceBundle


def evidence_to_envelope(e: Evidence, *, claim: str | None = None) -> dict[str, Any]:
    return {
        "claim": claim,
        "concept_uid": e.concept_uid,
        "resource_uri": e.resource_uri,
        "resource_version": e.resource_revision,
        "source_sha256": e.source_sha256,
        "locator": e.locator.to_stable_dict(),
        "retrieval": {
            "provider_id": e.provider_id,
            "provider_version": e.provider_version,
            "score": round(e.score, 4),
            "score_kind": e.score_kind,
        },
        "evidence_level": e.evidence_level,
        "evidence_text": e.evidence_text,
        "verified_at": e.verified_at,
        "_debug": {"native": e.locator.native},
    }


def bundle_to_envelope(b: EvidenceBundle, *, query: str, claim: str | None = None) -> dict[str, Any]:
    return {
        "query": query,
        "evidence": [evidence_to_envelope(e, claim=claim) for e in b.evidence],
        "rejected": [r.__dict__ for r in b.rejected],
        "warnings": [w.__dict__ for w in b.warnings],
        "omissions": b.omissions,
        "cost": b.cost,
        "max_evidence_level": b.max_level(),
    }
