"""Catalog SQL：精确身份解析、关系与 Binding。不回答长文本语义问题。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from ..spec import profile_v1 as profile


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
        rows = self.db.execute(
            "SELECT * FROM concepts WHERE aliases_json LIKE ? LIMIT ?", (f'%"{query}"%', limit)
        ).fetchall()
        if rows:
            return rows
        return self.db.execute(
            "SELECT * FROM concepts WHERE title LIKE ? OR uid LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()

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
