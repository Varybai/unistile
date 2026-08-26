"""L1 Schema Registry：登记过的类型才能驱动检索、权限与 Prompt 编译。

知识层是开放世界（可以不断出现新设备、新合同）；运行时协议层是封闭的——
只有在这里登记的 namespace / relation_type 才被 Catalog 与 Context Runtime 接受。
新增领域语义 = 改这个文件并发布新版本，不是让模型在会话里临时造类型。
"""

from __future__ import annotations

REGISTRY_VERSION = "unistile/schema-registry/v1"

# uid 的 namespace 白名单
NAMESPACES: frozenset[str] = frozenset(
    {
        "agreement",
        "equipment",
        "spec",
        "policy",
        "project",
        "organization",
        "workflow",
        "concept",
    }
)

# 已登记的关系类型。value 是端点 namespace 约束，None 表示不限制。
RELATION_TYPES: dict[str, dict[str, object]] = {
    "applies_to": {"source": None, "target": None, "inverse": "has_spec"},
    "part_of": {"source": None, "target": None, "inverse": "has_part"},
    "contract_for": {"source": {"agreement"}, "target": {"equipment"}, "inverse": "covered_by"},
    "amends": {"source": {"agreement"}, "target": {"agreement"}, "inverse": "amended_by"},
    "supersedes": {"source": None, "target": None, "inverse": "superseded_by"},
    "references": {"source": None, "target": None, "inverse": "referenced_by"},
}


def is_registered_namespace(ns: str) -> bool:
    return ns in NAMESPACES


def is_registered_relation(rel: str) -> bool:
    return rel in RELATION_TYPES
