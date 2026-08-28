"""Catalog SQL：精确身份解析、关系与 Binding。不回答长文本语义问题。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence

from ..spec import profile_v1 as profile


MAX_WINDOW_QUERY = 200      # 超过这个长度就不滑窗了：那不是在问某个实体，是在贴文章


def _similarity(query: str, value: str) -> float:
    """query 和某个身份串的相似度。确定性字符比对，不是语义相似度。

    三档，取最大：
      1. `value` 整个出现在 query 里         → 1.0（「马德旺的教育背景是什么？」）
      2. 在 query 上滑一个 len(value) 的窗   → 名字写错一个字又裹在问句里（「马旺的资料是什么？」）
      3. 整串比对                            → query 本身就是个身份串时

    没有第 2 档的话，整句提问 vs 短别名的比值会被问句长度稀释到阈值以下
    （「马旺的资料是什么？」vs「马德旺」只有 0.33），于是一个明明在库里的人
    会被报成「没有近似候选」—— 比不给候选更糟，那是在撒谎。
    """
    if not value:
        return 0.0
    if len(value) >= 2 and value in query:
        return 1.0

    best = SequenceMatcher(None, query, value).ratio()
    span = len(value)
    if 2 <= span < len(query) <= MAX_WINDOW_QUERY:
        for i in range(len(query) - span + 1):
            best = max(best, SequenceMatcher(None, query[i:i + span], value).ratio())
    return best


def _like_escape(value: str) -> str:
    """转义 LIKE 的元字符。反斜杠必须先转，否则会把后面转出来的斜杠再转一遍。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class BindingRow:
    binding_id: str
    concept_uid: str
    resource_uri: str
    resource_revision: int
    provider_id: str
    provider_version: str
    backend_object_id: str
    source_sha256: str
    indexed_sha256: str | None
    status: str
    role: str

    @property
    def is_stale(self) -> bool:
        return self.indexed_sha256 != self.source_sha256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CatalogStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
        self._migrate()

    def _migrate(self) -> None:
        """老 runtime 目录补列。runtime/ 本来就可重建，这里只是省一次全量 re-ingest。"""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(concepts)")}
        if "description" not in cols:
            self.db.execute("ALTER TABLE concepts ADD COLUMN description TEXT")
        ecols = {r[1] for r in self.db.execute("PRAGMA table_info(concept_edges)")}
        if "metadata" not in ecols:
            self.db.execute("ALTER TABLE concept_edges ADD COLUMN metadata TEXT")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------- Concept ----------
    def upsert_concept(self, c: profile.Concept, *, domain: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO concepts
               (uid, okf_path, title, description, type, status, evidence_class, media_type,
                resource_uri, source_sha256, external_id, aliases_json, domain, version, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(uid) DO UPDATE SET
                 okf_path=excluded.okf_path, title=excluded.title,
                 description=excluded.description, type=excluded.type,
                 status=excluded.status, evidence_class=excluded.evidence_class,
                 media_type=excluded.media_type, resource_uri=excluded.resource_uri,
                 source_sha256=excluded.source_sha256, external_id=excluded.external_id,
                 aliases_json=excluded.aliases_json, domain=excluded.domain,
                 version=concepts.version+1, content_hash=excluded.content_hash""",
            (
                c.uid, c.okf_path or "", c.title, c.description, c.type, c.status, c.evidence_class,
                c.media_type, c.resource, c.sha256, c.external_id,
                json.dumps(c.aliases, ensure_ascii=False), domain, 1, c.content_hash,
            ),
        )
        self.db.execute("DELETE FROM concept_edges WHERE source_uid=?", (c.uid,))
        for r in c.relations:
            self.db.execute(
                "INSERT OR REPLACE INTO concept_edges"
                " (source_uid, relation_type, target_uid, provenance, metadata) VALUES (?,?,?,?,?)",
                (c.uid, r.type, r.target, "okf-frontmatter",
                 json.dumps(r.metadata, ensure_ascii=False) if r.metadata else None),
            )
        self.db.commit()

    def get_concept(self, uid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM concepts WHERE uid=?", (uid,)).fetchone()

    def resolve(self, query: str, *, limit: int = 10) -> list[sqlite3.Row]:
        """身份解析优先级：uid > external_id > alias > 标题子串。语义相似度只产候选，不在这里。"""
        row = self.get_concept(query)
        if row:
            return [row]
        rows = self.db.execute("SELECT * FROM concepts WHERE external_id=?", (query,)).fetchall()
        if rows:
            return rows
        # query 里的 % 和 _ 必须转义：不转义时 resolve("50%") 会匹配全表，
        # 正好是这个方法声称禁止的「全库无界检索」。
        esc = _like_escape(query)
        # needle 必须按存进去时的同一种方式编码（upsert_concept 用 ensure_ascii=False），
        # 否则别名里带引号的条目永远匹配不上。json.dumps 顺便带上了定界引号，
        # 所以这仍然是「整条别名相等」而不是子串命中。
        alias_needle = _like_escape(json.dumps(query, ensure_ascii=False))
        rows = self.db.execute(
            r"SELECT * FROM concepts WHERE aliases_json LIKE ? ESCAPE '\' LIMIT ?",
            (f"%{alias_needle}%", limit),
        ).fetchall()
        if rows:
            return rows
        return self.db.execute(
            r"SELECT * FROM concepts WHERE title LIKE ? ESCAPE '\' OR uid LIKE ? ESCAPE '\' LIMIT ?",
            (f"%{esc}%", f"%{esc}%", limit),
        ).fetchall()

    def near_matches(
        self, query: str, *, limit: int = 5, threshold: float = 0.6
    ) -> list[dict[str, object]]:
        """resolve() 落空时的确定性近似候选。

        只比对 Catalog 的身份字段（title / aliases / external_id / uid），**不碰文本平面**——
        在这里搜正文等于把无界检索从后门放回来，那是 Provider 在 scope 内该做的事。

        用 difflib 的字符序列相似度，不是语义相似度：同样的库同样的输入永远同样的输出。
        """
        query = (query or "").strip()
        if not query:
            return []

        scored: list[tuple[float, str, dict[str, object]]] = []
        for row in self.db.execute("SELECT * FROM concepts").fetchall():
            candidates: list[tuple[str, str]] = [("title", row["title"]), ("uid", row["uid"])]
            if row["external_id"]:
                candidates.append(("external_id", row["external_id"]))
            for alias in json.loads(row["aliases_json"] or "[]"):
                candidates.append(("alias", alias))

            best_ratio, best_field, best_value = 0.0, "", ""
            for field_name, value in candidates:
                if not value:
                    continue
                value = str(value)
                ratio = _similarity(query, value)
                if ratio > best_ratio:
                    best_ratio, best_field, best_value = ratio, field_name, value

            if best_ratio >= threshold:
                scored.append(
                    (best_ratio, row["uid"], {
                        "uid": row["uid"],
                        "title": row["title"],
                        "matched_on": best_field,
                        "matched_value": best_value,
                        "ratio": round(best_ratio, 4),
                    })
                )

        # (相似度降序, uid 升序) —— uid 是主键，所以排序完全确定，不依赖行序。
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [payload for _, _, payload in scored[:limit]]

    def edges_from(self, uid: str, relation_types: Sequence[str] | None = None) -> list[sqlite3.Row]:
        if relation_types:
            marks = ",".join("?" * len(relation_types))
            return self.db.execute(
                f"SELECT * FROM concept_edges WHERE source_uid=? AND relation_type IN ({marks})",
                (uid, *relation_types),
            ).fetchall()
        return self.db.execute("SELECT * FROM concept_edges WHERE source_uid=?", (uid,)).fetchall()

    def edges_to(self, uid: str, relation_types: Sequence[str] | None = None) -> list[sqlite3.Row]:
        if relation_types:
            marks = ",".join("?" * len(relation_types))
            return self.db.execute(
                f"SELECT * FROM concept_edges WHERE target_uid=? AND relation_type IN ({marks})",
                (uid, *relation_types),
            ).fetchall()
        return self.db.execute("SELECT * FROM concept_edges WHERE target_uid=?", (uid,)).fetchall()

    # ---------- Resource revision ----------
    def upsert_revision(
        self, resource_uri: str, revision: int, source_sha256: str,
        normalized_text_sha256: str, extractor_version: str,
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO resource_revisions VALUES (?,?,?,?,?)",
            (resource_uri, revision, source_sha256, normalized_text_sha256, extractor_version),
        )
        self.db.commit()

    def get_revision(self, resource_uri: str, revision: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM resource_revisions WHERE resource_uri=? AND revision=?",
            (resource_uri, revision),
        ).fetchone()

    # ---------- Binding ----------
    def upsert_binding(
        self, *, binding_id: str, concept_uid: str, resource_uri: str, resource_revision: int,
        provider_id: str, provider_version: str, backend_object_id: str, source_sha256: str,
        indexed_sha256: str | None, status: str, role: str = "primary",
    ) -> None:
        self.db.execute(
            """INSERT INTO resource_bindings
               (binding_id, concept_uid, resource_uri, resource_revision, provider_id, provider_version,
                backend_object_id, source_sha256, indexed_sha256, status, role, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(binding_id) DO UPDATE SET
                 backend_object_id=excluded.backend_object_id,
                 source_sha256=excluded.source_sha256,
                 indexed_sha256=excluded.indexed_sha256,
                 status=excluded.status, role=excluded.role, updated_at=excluded.updated_at""",
            (binding_id, concept_uid, resource_uri, resource_revision, provider_id, provider_version,
             backend_object_id, source_sha256, indexed_sha256, status, role, _now()),
        )
        self.db.commit()

    def bindings_for(
        self, concept_uids: Iterable[str], *, provider_id: str | None = None, role: str | None = "primary"
    ) -> list[BindingRow]:
        uids = list(concept_uids)
        if not uids:
            return []
        marks = ",".join("?" * len(uids))
        sql = f"SELECT * FROM resource_bindings WHERE concept_uid IN ({marks})"
        args: list[object] = list(uids)
        if provider_id:
            sql += " AND provider_id=?"
            args.append(provider_id)
        if role:
            sql += " AND role=?"
            args.append(role)
        rows = self.db.execute(sql, args).fetchall()
        return [BindingRow(**{k: r[k] for k in BindingRow.__annotations__}) for r in rows]

    def get_binding(self, binding_id: str) -> BindingRow | None:
        r = self.db.execute("SELECT * FROM resource_bindings WHERE binding_id=?", (binding_id,)).fetchone()
        return BindingRow(**{k: r[k] for k in BindingRow.__annotations__}) if r else None

    def set_role(self, binding_id: str, role: str) -> None:
        self.db.execute(
            "UPDATE resource_bindings SET role=?, updated_at=? WHERE binding_id=?", (role, _now(), binding_id)
        )
        self.db.commit()

    def mark_stale(self, binding_id: str) -> None:
        self.db.execute(
            "UPDATE resource_bindings SET status='stale', updated_at=? WHERE binding_id=?", (_now(), binding_id)
        )
        self.db.commit()
