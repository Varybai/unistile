"""投影：同一个 Concept 出现在多棵导航树下，Canonical 仍只有一份。

Projection 回答"为了导航目的放在哪里"；concept_edges 回答"语义上是什么关系"。
两者不能互相替代 —— 这里同时断言两点。
"""

from __future__ import annotations

import pytest

from unistile.app import Runtime
from unistile.projections import ProjectionError, children, projections_of, rebuild
from unistile.spec.validator import validate_bundle

AGR = "kn:agreement:AGR0048"


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


def test_same_concept_in_three_projections(rt):
    pids = {r["projection_id"] for r in projections_of(rt.catalog, AGR)}
    assert pids == {"business", "document-collection", "lifecycle"}


def test_canonical_stays_single(rt):
    rows = rt.catalog.db.execute("SELECT count(*) FROM concepts WHERE uid=?", (AGR,)).fetchone()
    assert rows[0] == 1, "多个投影节点不得复制 Canonical Concept"


def test_projection_is_not_relation(rt):
    """AGR0048 在 business 下挂在"供应商 X"，但没有任何 supplier 关系边。"""
    node = next(r for r in projections_of(rt.catalog, AGR) if r["projection_id"] == "business")
    assert node["parent_node_id"] == "alpha:supplier-x"
    edge_types = {r["relation_type"] for r in rt.catalog.edges_from(AGR)}
    assert edge_types == {"contract_for"}, "导航位置不应变成语义关系"


def test_query_backed_projection_tracks_concepts(rt):
    rows, total = children(rt.catalog, "document-collection", None)
    assert {r["label"] for r in rows} == {"contracts", "equipment"}
    assert total == 2


def test_children_reports_omission(rt):
    rows, total = children(rt.catalog, "document-collection", "dc:equipment", limit=1)
    assert len(rows) == 1
    assert total >= 2, "总数必须如实给出，Agent 不能把没显示当成不存在"


def test_concept_node_label_uses_title_not_uid(rt):
    rows, _ = children(rt.catalog, "business", "alpha:supplier-x")
    assert all(not r["label"].startswith("kn:") for r in rows)
    assert "AGR0048 主设备采购协议" in {r["label"] for r in rows}


def test_view_metadata_from_relation_edge(rt):
    """clause 这类关系事实存在边上，规则派生时取回来，不靠手写 view_metadata。"""
    rows, _ = children(rt.catalog, "lifecycle", "lc:amended")
    assert rows[0]["concept_uid"] == AGR
    assert "7.2" in rows[0]["view_metadata"]
    assert "kn:agreement:Supplement-02" in rows[0]["view_metadata"]


def test_lifecycle_covers_every_concept(rt):
    """规则派生不会漏项 —— 手写清单会。"""
    total = rt.catalog.db.execute("SELECT count(*) FROM concepts").fetchone()[0]
    placed = rt.catalog.db.execute(
        "SELECT count(DISTINCT concept_uid) FROM projection_nodes"
        " WHERE projection_id='lifecycle' AND concept_uid IS NOT NULL"
    ).fetchone()[0]
    assert placed == total


def test_new_document_auto_enters_lifecycle(rt, bundle, tmp_path):
    from unistile.ingest_new import add_document

    src = tmp_path / "新协议.md"
    src.write_text("# AGR0099\n\n## 1. 范围\n\n备件供应。\n", encoding="utf-8")
    add_document(bundle, src, uid="kn:agreement:AGR0099", title="AGR0099 备件供应协议",
                 domain="contracts", concept_type="Agreement", description="备件供应。")
    rt.ingest()
    pids = {r["projection_id"] for r in projections_of(rt.catalog, "kn:agreement:AGR0099")}
    assert "lifecycle" in pids and "document-collection" in pids
    assert "business" not in pids, "手写投影不会自动收录，这是预期行为"


def test_empty_group_not_rendered(rt):
    """没有 draft/retired 的 Concept 时，这两个分组不应出现在导航里。"""
    rows, _ = children(rt.catalog, "lifecycle", None)
    labels = {r["node_id"] for r in rows}
    assert labels == {"lc:current", "lc:amended"}


def test_rebuild_is_idempotent(rt, bundle):
    a = rebuild(bundle, rt.catalog)
    b = rebuild(bundle, rt.catalog)
    assert a == b


def test_dangling_concept_ref_rejected(rt, bundle):
    p = bundle / "projections" / "business.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace(AGR, "kn:agreement:GHOST"), encoding="utf-8")
    codes = {f.code for f in validate_bundle(bundle).findings}
    assert "L2.projection_ref" in codes
    with pytest.raises(ProjectionError):
        rebuild(bundle, rt.catalog)


def test_duplicate_node_id_rejected(rt, bundle):
    p = bundle / "projections" / "business.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("id: alpha:supplier-x:S02", "id: alpha:supplier-x:AGR0048"), encoding="utf-8")
    codes = {f.code for f in validate_bundle(bundle).findings}
    assert "L2.projection" in codes


def test_missing_projections_dir_is_fine(rt, bundle):
    import shutil

    shutil.rmtree(bundle / "projections")
    out = rebuild(bundle, rt.catalog)
    assert sorted(out) == ["document-collection", "lifecycle"], "没有 YAML 时仍有规则派生的投影"
