"""local-fts/v1 —— POC 主力 Provider。

零外部依赖：SQLite FTS5 + trigram 分词（对中文可用、行为确定，不引入分词器变量）。
在归一化文本平面之上建索引，因此 candidate 的 char_span 可以被 Runtime 精确回读。

不提供：向量/混合检索、rerank、MMR、多轮 agentic 查询、图片理解。
这些由 capabilities() 如实声明缺失，Runtime 自动降级或把 obligation 判为 blocked。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from ..contract import (
    BindRequest,
    BindResult,
    ContextExpandRequest,
    CostRecord,
    EvidenceSearchRequest,
    EvidenceSearchResult,
    HealthReport,
    Locator,
    OmissionInfo,
    ProviderCapabilities,
    ProviderWarning,
    RankedEvidenceCandidate,
)
from ..errors import ProviderInternal, ScopeError

PROVIDER_ID = "local-fts"
PROVIDER_VERSION = "1.0.0"

MAX_CHUNK_CHARS = 800
CHUNK_OVERLAP = 150
MIN_TERM_LEN = 3          # trigram 分词下，短于 3 字符的词无法匹配
MAX_TERMS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id   TEXT PRIMARY KEY,
  binding_id TEXT NOT NULL,
  concept_uid TEXT NOT NULL,
  resource_uri TEXT NOT NULL,
  resource_revision INTEGER NOT NULL,
  normalized_text_sha256 TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  ordinal    INTEGER NOT NULL,
  char_start INTEGER NOT NULL,
  char_end   INTEGER NOT NULL,
  page       INTEGER,
  section_path_json TEXT NOT NULL,
  chunk_sha256 TEXT NOT NULL,
  text       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_binding ON chunks(binding_id, ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='trigram');
"""

_CJK = r"一-鿿㐀-䶿"
_SPLIT_RE = re.compile(rf"[^\w{_CJK}]+")


def terms_from_query(query: str) -> list[str]:
    """确定性的查询词抽取。

    非 CJK 片段按空白/标点切分；长于 3 字的 CJK 片段额外产生 3 字滑窗，
    因为 trigram 分词下整句短语匹配过严（"质保期是多久" 作为短语在原文中并不存在）。
    结果去重后按出现顺序保留，最多 MAX_TERMS 个。
    """
    terms: list[str] = []
    for part in _SPLIT_RE.split(query):
        if len(part) < MIN_TERM_LEN:
            continue
        terms.append(part)
        if re.fullmatch(rf"[{_CJK}]+", part) and len(part) > MIN_TERM_LEN:
            for i in range(0, len(part) - 2):
                terms.append(part[i : i + 3])
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= MAX_TERMS:
            break
    return out


def _match_expr(terms: list[str]) -> str:
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _chunk_spans(text: str, outline: tuple[tuple[tuple[str, ...], int, int], ...]):
    """结构感知切块：按标题切段，超长段落再滑窗。"""
    if outline:
        heads = sorted({(start, path) for path, start, _ in outline})
        spans: list[tuple[int, int, tuple[str, ...]]] = []
        if heads and heads[0][0] > 0:
            spans.append((0, heads[0][0], ()))
        for i, (start, path) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
            spans.append((start, end, path))
    else:
        spans = [(0, len(text), ())]

    for start, end, path in spans:
        if end - start <= MAX_CHUNK_CHARS:
            if text[start:end].strip():
                yield start, end, path
            continue
        pos = start
        while pos < end:
            stop = min(pos + MAX_CHUNK_CHARS, end)
            if text[pos:stop].strip():
                yield pos, stop, path
            if stop >= end:
                break
            pos = stop - CHUNK_OVERLAP


class LocalFtsProvider:
    def __init__(self, index_path: str | Path):
        self.index_path = Path(index_path)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.index_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

    # ---------- 能力 ----------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=PROVIDER_ID,
            provider_version=PROVIDER_VERSION,
            query_expansion="none",
            retrieval_modes=("keyword",),
            rerank=False,
            diversity="none",
            parent_child_expansion=False,
            neighbor_expansion=True,
            image_understanding="none",
            locator_kinds=("char_span", "section_path", "page"),
            supports_slice_readback=True,   # locator 足以被 Runtime 从归一化文本平面精确回读
            determinism="deterministic",
            max_scope_size=256,
            max_rounds=1,
            media_types=("text/markdown", "text/plain"),
        )

    # ---------- 索引生命周期 ----------
    @staticmethod
    def _page_of(offset: int, pages) -> int | None:
        for number, start, end in pages:
            if start <= offset < end:
                return number
        return None            # 抽取器没给页边界 —— 留空，不猜

    def bind(self, req: BindRequest) -> BindResult:
        self.unbind(req.binding_id)
        n = 0
        for ordinal, (start, end, path) in enumerate(_chunk_spans(req.text, req.outline)):
            body = req.text[start:end]
            chunk_id = f"{req.binding_id}#{ordinal:04d}"
            cur = self.db.execute(
                """INSERT INTO chunks (chunk_id, binding_id, concept_uid, resource_uri, resource_revision,
                       normalized_text_sha256, extractor_version, ordinal, char_start, char_end, page,
                       section_path_json, chunk_sha256, text)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chunk_id, req.binding_id, req.concept_uid, req.resource_uri, req.resource_revision,
                    req.normalized_text_sha256, req.extractor_version, ordinal, start, end,
                    self._page_of(start, req.pages),
                    json.dumps(list(path), ensure_ascii=False),
                    "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(), body,
                ),
            )
            self.db.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cur.lastrowid, body))
            n += 1
        self.db.commit()
        return BindResult(
            binding_id=req.binding_id,
            backend_object_id=f"lfts:{req.binding_id}",
            indexed_sha256=req.source_sha256,
            status="ready",
            units=n,
        )

    def reindex(self, binding_id: str) -> BindResult:
        raise ProviderInternal("local-fts 不保存原始资源；reindex 请由 Runtime 重新调用 bind()")

    def unbind(self, binding_id: str) -> None:
        rows = self.db.execute("SELECT rowid FROM chunks WHERE binding_id=?", (binding_id,)).fetchall()
        for r in rows:
            self.db.execute("DELETE FROM chunks_fts WHERE rowid=?", (r["rowid"],))
        self.db.execute("DELETE FROM chunks WHERE binding_id=?", (binding_id,))
        self.db.commit()

    # ---------- 检索 ----------
    def search(self, req: EvidenceSearchRequest) -> EvidenceSearchResult:
        t0 = time.perf_counter()
        scope = tuple(req.scope_binding_ids)
        if not scope:
            raise ScopeError("scope_binding_ids 为空；文档证据检索必须由 Runtime 解析出非空范围")
        if len(scope) > self.capabilities().max_scope_size:
            raise ScopeError(f"scope 超过 {self.capabilities().max_scope_size} 个 binding")

        known = {
            r["binding_id"]
            for r in self.db.execute(
                f"SELECT DISTINCT binding_id FROM chunks WHERE binding_id IN ({','.join('?' * len(scope))})",
                scope,
            ).fetchall()
        }
        unknown = [b for b in scope if b not in known]
        warnings: list[ProviderWarning] = [
            ProviderWarning("scope.unbound", f"binding 未在本 Provider 建立索引：{b}", b) for b in unknown
        ]
        if not known:
            raise ScopeError(f"scope 内没有任何已索引的 binding：{list(scope)}")

        terms = terms_from_query(req.query)
        if not terms:
            return EvidenceSearchResult(
                candidates=(),
                omission=OmissionInfo(0, 0, reason=f"查询无可用检索词（trigram 需要 ≥{MIN_TERM_LEN} 字符）"),
                actual_cost=CostRecord(calls=1, latency_ms=int((time.perf_counter() - t0) * 1000)),
                warnings=tuple(warnings),
            )

        marks = ",".join("?" * len(known))
        sql = (
            "SELECT c.*, bm25(chunks_fts) AS bm FROM chunks_fts "
            "JOIN chunks c ON c.rowid = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND c.binding_id IN ({marks}) "
            "ORDER BY bm ASC, c.binding_id ASC, c.ordinal ASC"
        )
        try:
            rows = self.db.execute(sql, (_match_expr(terms), *sorted(known))).fetchall()
        except sqlite3.OperationalError as e:  # noqa: BLE001
            raise ProviderInternal(f"FTS 查询失败：{e}") from e

        if req.filters and req.filters.section_prefix:
            pref = list(req.filters.section_prefix)
            rows = [r for r in rows if json.loads(r["section_path_json"])[: len(pref)] == pref]

        total = len(rows)
        limit = max(1, req.budget.max_candidates)
        kept = rows[:limit]
        cands = tuple(self._to_candidate(r) for r in kept)
        return EvidenceSearchResult(
            candidates=cands,
            omission=OmissionInfo(
                total_matched=total,
                returned=len(cands),
                truncated_by="max_candidates" if total > len(cands) else None,
                reason=f"命中 {total} 段，按预算返回前 {len(cands)} 段" if total > len(cands) else None,
            ),
            actual_cost=CostRecord(
                calls=1,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                evidence_reads=len(cands),
                tokens=sum(len(c.text) for c in cands) // 2,
            ),
            warnings=tuple(warnings),
        )

    def expand(self, req: ContextExpandRequest) -> EvidenceSearchResult:
        t0 = time.perf_counter()
        if req.direction == "parent":
            raise ProviderInternal("local-fts 未声明 parent_child_expansion，Runtime 不应请求该方向")
        out: list[RankedEvidenceCandidate] = []
        for cid in req.candidate_ids:
            row = self.db.execute("SELECT * FROM chunks WHERE chunk_id=?", (cid,)).fetchone()
            if row is None:
                raise ScopeError(f"未知 candidate_id：{cid}")
            lo, hi = row["ordinal"] - req.span, row["ordinal"] + req.span
            rows = self.db.execute(
                "SELECT * FROM chunks WHERE binding_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal",
                (row["binding_id"], lo, hi),
            ).fetchall()
            out.extend(self._to_candidate(r, score_kind="opaque", score=0.0) for r in rows)
        return EvidenceSearchResult(
            candidates=tuple(out),
            omission=OmissionInfo(total_matched=len(out), returned=len(out)),
            actual_cost=CostRecord(calls=1, latency_ms=int((time.perf_counter() - t0) * 1000),
                                   evidence_reads=len(out)),
        )

    def health(self) -> HealthReport:
        n = self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        return HealthReport(PROVIDER_ID, True, f"{n} chunks indexed at {self.index_path}")

    # ---------- 内部 ----------
    def _to_candidate(self, r: sqlite3.Row, *, score_kind: str = "bm25", score: float | None = None):
        loc = Locator(
            kind="char_span",
            resource_uri=r["resource_uri"],
            resource_revision=r["resource_revision"],
            normalized_text_sha256=r["normalized_text_sha256"],
            extractor_version=r["extractor_version"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            page=r["page"],
            section_path=tuple(json.loads(r["section_path_json"])),
            content_sha256=r["chunk_sha256"],
            native={"chunk_id": r["chunk_id"], "ordinal": r["ordinal"]},
        )
        s = score if score is not None else -float(r["bm"])
        return RankedEvidenceCandidate(
            candidate_id=r["chunk_id"],
            binding_id=r["binding_id"],
            concept_uid=r["concept_uid"],
            resource_uri=r["resource_uri"],
            resource_revision=r["resource_revision"],
            text=r["text"],
            locator=loc,
            score=s,
            score_kind=score_kind,  # type: ignore[arg-type]
            provider_id=PROVIDER_ID,
            provider_version=PROVIDER_VERSION,
            rerank_score=None,      # 未声明 rerank 能力，此字段必须为空
        )
