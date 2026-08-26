"""OKF Profile `unistile/okf-profile/v1`。

分层：
  L0 上游兼容   type/title/description/resource/tags/status/generated/sources/stale_after/verified
  L1 治理扩展   uid/external_id/sha256/aliases/relations/evidence_class/media_type
  L2 提示性     projections/policy_hints —— 只用于 Head 展示，不作为权限或路由依据
  禁止          任何后端信息（access/provider_id/knowledge_id/...）——属于可重建的运行时 Binding
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROFILE_VERSION = "unistile/okf-profile/v1"

STATUS_VALUES = frozenset({"draft", "stable", "deprecated", "superseded"})
EVIDENCE_CLASSES = frozenset({"document", "structured", "computation", "image", "code"})

REQUIRED_L0 = ("type", "title", "status")
REQUIRED_L1 = ("uid", "evidence_class")
OPTIONAL_L0 = ("description", "resource", "tags", "generated", "sources", "stale_after", "verified")
OPTIONAL_L1 = ("external_id", "sha256", "aliases", "relations", "media_type", "resource_revision")
OPTIONAL_L2 = ("projections", "policy_hints")

# 出现即报错：这些属于 resource_bindings 表，写进 Canonical Concept 会让派生物污染真相源，
# 并使同一 Concept 无法同时绑定多个 Provider（热插拔失效）。
FORBIDDEN_KEYS = (
    "access",
    "binding",
    "binding_key",
    "binding_id",
    "provider",
    "provider_id",
    "backend",
    "backend_object_id",
    "knowledge_id",
    "knowledge_ids",
    "embedding_model",
    "chunk_size",
    "indexed_sha256",
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(?P<fm>.*?)\r?\n---\r?\n?(?P<body>.*)\Z", re.S)


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Relation:
    type: str
    target: str
    metadata: dict[str, Any] | None = None   # 关系上的治理事实，如 amends 修改了哪一条


@dataclass
class Concept:
    """Canonical Concept —— 知识治理真相。不含任何后端信息。"""

    uid: str
    type: str
    title: str
    status: str
    evidence_class: str
    okf_path: str | None = None
    description: str | None = None
    resource: str | None = None
    media_type: str | None = None
    sha256: str | None = None
    resource_revision: int = 1
    external_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    stale_after: str | None = None
    verified: Any = None
    raw: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def content_hash(self) -> str:
        """Concept 文件本身的哈希（与 resource 字节的 sha256 是两回事）。"""
        payload = yaml.safe_dump(self.raw, allow_unicode=True, sort_keys=True) + self.body
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def head(self) -> dict[str, Any]:
        """注入模型上下文的最小 Head —— 不含正文、不含后端信息。"""
        return {
            "uid": self.uid,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "evidence_class": self.evidence_class,
            "description": self.description,
        }


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ProfileError("缺少 YAML frontmatter（文件必须以 --- 开头）")
    data = yaml.safe_load(m["fm"])
    if not isinstance(data, dict):
        raise ProfileError("frontmatter 不是映射结构")
    return data, m["body"]


def from_frontmatter(data: dict[str, Any], *, okf_path: str | None = None, body: str = "") -> Concept:
    rels = []
    for r in data.get("relations") or []:
        if not isinstance(r, dict) or "type" not in r or "target" not in r:
            raise ProfileError(f"relations 条目结构非法：{r!r}")
        md = r.get("metadata")
        if md is not None and not isinstance(md, dict):
            raise ProfileError(f"relations.metadata 必须是映射：{md!r}")
        rels.append(Relation(str(r["type"]), str(r["target"]), md))
    return Concept(
        uid=str(data.get("uid", "")),
        type=str(data.get("type", "")),
        title=str(data.get("title", "")),
        status=str(data.get("status", "")),
        evidence_class=str(data.get("evidence_class", "")),
        okf_path=okf_path,
        description=data.get("description"),
        resource=data.get("resource"),
        media_type=data.get("media_type"),
        sha256=data.get("sha256"),
        resource_revision=int(data.get("resource_revision", 1)),
        external_id=data.get("external_id"),
        aliases=list(data.get("aliases") or []),
        tags=list(data.get("tags") or []),
        relations=rels,
        stale_after=data.get("stale_after"),
        verified=data.get("verified"),
        raw=data,
        body=body,
    )


def load_concept(path: str | Path) -> Concept:
    p = Path(path)
    data, body = split_frontmatter(p.read_text(encoding="utf-8"))
    return from_frontmatter(data, okf_path=str(p), body=body)


def resolve_resource(concept: Concept, bundle_root: str | Path) -> Path | None:
    """asset://documents/x.md -> <bundle>/assets/documents/x.md"""
    if not concept.resource:
        return None
    uri = concept.resource
    if uri.startswith("asset://"):
        return Path(bundle_root) / "assets" / uri[len("asset://") :]
    return None


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()
