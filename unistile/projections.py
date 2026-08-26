"""投影：同一个 Concept 出现在多棵导航树下，Canonical Concept 仍只有一个。

Projection 回答"为了某种导航目的，这个 Concept 放在哪里"；
concept_edges 的类型化关系回答"两个 Concept 在语义上是什么关系"。两者不可互相替代。

两类来源：
  materialized  knowledge/projections/*.yaml —— 人/Agent 维护，进 git，是真相源
  query_backed  由 Catalog 规则确定性派生 —— 零维护，随 Concept 自动更新

投影树里的 part_of 必须无环；节点引用的 concept_uid 必须存在。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml


class ProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class FlatNode:
    projection_id: str
    node_id: str
    concept_uid: str | None
    parent_node_id: str | None
    label: str
    rank: int
    view_metadata: str | None


def _flatten(pid: str, nodes: list[dict], parent: str | None, seen: set[str]) -> Iterator[FlatNode]:
    import json

    for rank, n in enumerate(nodes):
        if not isinstance(n, dict) or "id" not in n:
            raise ProjectionError(f"{pid}: 节点缺少 id：{n!r}")
        nid = str(n["id"])
        if nid in seen:
            raise ProjectionError(f"{pid}: node_id 重复或成环：{nid}")
        seen.add(nid)
        concept = n.get("concept")
        label = str(n.get("label") or concept or nid)
        vm = n.get("view_metadata")
        yield FlatNode(pid, nid, str(concept) if concept else None, parent, label, rank * 10,
                       json.dumps(vm, ensure_ascii=False) if vm else None)
        yield from _flatten(pid, n.get("children") or [], nid, seen)


def load_definition(path: str | Path) -> tuple[dict[str, Any], list[FlatNode]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "projection_id" not in data:
        raise ProjectionError(f"{path}: 缺少 projection_id")
    pid = str(data["projection_id"])
    flat = list(_flatten(pid, data.get("nodes") or [], None, set()))
    meta = {
        "projection_id": pid,
        "title": str(data.get("title") or pid),
        "description": data.get("description"),
        "kind": "materialized",
        "source": str(path),
    }
    return meta, flat


# ---------- query_backed：由 Catalog 规则派生 ----------
def derive_document_collection(catalog) -> tuple[dict[str, Any], list[FlatNode]]:
    """资料组织投影：domain → type → Concept。随 Concept 自动更新，无需维护。"""
    pid = "document-collection"
    flat: list[FlatNode] = []
    rows = catalog.db.execute(
        "SELECT uid, title, type, domain FROM concepts WHERE domain IS NOT NULL"
        " ORDER BY domain, type, uid"
    ).fetchall()
    domains: dict[str, dict[str, list]] = {}
    for r in rows:
        domains.setdefault(r["domain"], {}).setdefault(r["type"], []).append(r)
    for di, (domain, types) in enumerate(sorted(domains.items())):
        dnode = f"dc:{domain}"
        flat.append(FlatNode(pid, dnode, None, None, domain, di * 10, None))
        for ti, (t, items) in enumerate(sorted(types.items())):
            tnode = f"{dnode}:{t}"
            flat.append(FlatNode(pid, tnode, None, dnode, t, ti * 10, None))
            for ci, r in enumerate(items):
                flat.append(FlatNode(pid, f"{tnode}:{r['uid']}", r["uid"], tnode, r["title"], ci * 10, None))
    meta = {"projection_id": pid, "title": "资料组织视图", "description": "domain → type → Concept",
            "kind": "query_backed", "source": "rule:document-collection"}
    return meta, flat


def derive_lifecycle(catalog) -> tuple[dict[str, Any], list[FlatNode]]:
    """时效视图：按治理状态分组，回答"现在该以哪份为准"。

    分组依据全部来自 Catalog，不手写清单：
      retired  status ∈ {deprecated, superseded}，或被 supersedes 边指向
      amended  被 amends 边指向 —— 单独引用它会漏掉修改
      draft    status = draft
      current  其余 stable

    手写清单会漏项且无声（POC 早期的 lifecycle.yaml 就漏了验收规范）；规则不会。
    """
    import json

    pid = "lifecycle"
    rows = catalog.db.execute(
        "SELECT uid, title, status,"
        " (SELECT count(*) FROM concept_edges e WHERE e.target_uid=c.uid AND e.relation_type='amends')"
        "   AS n_amends,"
        " (SELECT count(*) FROM concept_edges e WHERE e.target_uid=c.uid AND e.relation_type='supersedes')"
        "   AS n_supersedes"
        " FROM concepts c ORDER BY uid"
    ).fetchall()

    groups = {
        "current": "当前有效",
        "amended": "已被修改",
        "retired": "已停用或被取代",
        "draft": "草稿",
    }
    buckets: dict[str, list] = {k: [] for k in groups}
    for r in rows:
        if r["status"] in ("deprecated", "superseded") or r["n_supersedes"]:
            buckets["retired"].append(r)
        elif r["n_amends"]:
            buckets["amended"].append(r)
        elif r["status"] == "draft":
            buckets["draft"].append(r)
        else:
            buckets["current"].append(r)

    flat: list[FlatNode] = []
    for gi, (gid, glabel) in enumerate(groups.items()):
        items = buckets[gid]
        if not items:
            continue    # 空分组不进导航，避免 Agent 展开空节点
        flat.append(FlatNode(pid, f"lc:{gid}", None, None, f"{glabel}（{len(items)}）", gi * 10, None))
        for ci, r in enumerate(items):
            vm = None
            edges = catalog.db.execute(
                "SELECT source_uid, relation_type, metadata FROM concept_edges"
                " WHERE target_uid=? AND relation_type IN ('amends','supersedes')",
                (r["uid"],),
            ).fetchall()
            if edges:
                vm = json.dumps(
                    {
                        "changed_by": [
                            {"uid": e["source_uid"], "relation": e["relation_type"],
                             **(json.loads(e["metadata"]) if e["metadata"] else {})}
                            for e in edges
                        ]
                    },
                    ensure_ascii=False,
                )
            flat.append(FlatNode(pid, f"lc:{gid}:{r['uid']}", r["uid"], f"lc:{gid}", r["title"], ci * 10, vm))

    meta = {"projection_id": pid, "title": "时效视图",
            "description": "按治理状态与 amends/supersedes 关系分组",
            "kind": "query_backed", "source": "rule:lifecycle"}
    return meta, flat


def rebuild(bundle_root: str | Path, catalog) -> dict[str, int]:
    """重建全部投影。materialized 来自 YAML，query_backed 来自规则。"""
    catalog.db.execute("DELETE FROM projection_nodes")
    catalog.db.execute("DELETE FROM projections")

    defs: list[tuple[dict, list[FlatNode]]] = [
        derive_document_collection(catalog),
        derive_lifecycle(catalog),
    ]
    pdir = Path(bundle_root) / "projections"
    if pdir.exists():
        for f in sorted(pdir.glob("*.yaml")):
            defs.append(load_definition(f))

    titles = {r[0]: r[1] for r in catalog.db.execute("SELECT uid, title FROM concepts")}
    known = set(titles)
    out: dict[str, int] = {}
    for meta, flat in defs:
        for n in flat:
            if n.concept_uid and n.concept_uid not in known:
                raise ProjectionError(
                    f"{meta['projection_id']}/{n.node_id} 引用了不存在的 Concept：{n.concept_uid}"
                )
        catalog.db.execute(
            "INSERT OR REPLACE INTO projections VALUES (?,?,?,?,?)",
            (meta["projection_id"], meta["title"], meta["description"], meta["kind"], meta["source"]),
        )
        catalog.db.executemany(
            "INSERT OR REPLACE INTO projection_nodes"
            " (projection_id,node_id,concept_uid,parent_node_id,label,rank,view_metadata)"
            " VALUES (?,?,?,?,?,?,?)",
            [(n.projection_id, n.node_id, n.concept_uid, n.parent_node_id,
              # 未显式给 label 的 Concept 节点，用它的标题，别在导航里显示裸 uid
              titles.get(n.concept_uid, n.label) if n.label == n.concept_uid else n.label,
              n.rank, n.view_metadata) for n in flat],
        )
        out[meta["projection_id"]] = len(flat)
    catalog.db.commit()
    return out


# ---------- 导航查询 ----------
def children(catalog, projection_id: str, parent_node_id: str | None, *, limit: int = 20, offset: int = 0):
    """一层导航 + omission 统计：Agent 不能把"没显示"当成"不存在"。"""
    where = "parent_node_id IS NULL" if parent_node_id is None else "parent_node_id = ?"
    args: list[Any] = [projection_id] if parent_node_id is None else [projection_id, parent_node_id]
    total = catalog.db.execute(
        f"SELECT count(*) FROM projection_nodes WHERE projection_id=? AND {where}", args
    ).fetchone()[0]
    rows = catalog.db.execute(
        f"SELECT n.*, c.description, c.status, c.evidence_class FROM projection_nodes n"
        f" LEFT JOIN concepts c ON c.uid = n.concept_uid"
        f" WHERE n.projection_id=? AND {where} ORDER BY n.rank, n.node_id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ).fetchall()
    return rows, total


def projections_of(catalog, concept_uid: str):
    """同一个 Concept 出现在哪些投影的哪些位置。"""
    return catalog.db.execute(
        "SELECT p.projection_id, p.title, n.node_id, n.parent_node_id, n.label"
        " FROM projection_nodes n JOIN projections p USING (projection_id)"
        " WHERE n.concept_uid=? ORDER BY p.projection_id",
        (concept_uid,),
    ).fetchall()
