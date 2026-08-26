"""index.md 是派生物，且格式与上游 OKF SPEC §8 对齐。

上游规则：可选、可自动生成、缺失不得导致 bundle 被拒；只有根 index 可带 frontmatter
（且只放 okf_version）；条目格式 `* [Title](url) - description`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unistile.app import Runtime
from unistile.indexgen import GENERATED_MARK, OKF_VERSION


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


def test_root_index_carries_only_okf_version(rt, bundle):
    text = (bundle / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm = text.split("---")[1].strip()
    assert fm == f'okf_version: "{OKF_VERSION}"', "根索引 frontmatter 只放 okf_version"


def test_domain_index_has_no_frontmatter(rt, bundle):
    for idx in (bundle / "domains").glob("*/index.md"):
        assert not idx.read_text(encoding="utf-8").startswith("---")


def test_entries_carry_description(rt, bundle):
    text = (bundle / "domains" / "contracts" / "index.md").read_text(encoding="utf-8")
    entries = [l for l in text.splitlines() if l.startswith("* [")]
    assert entries, "领域索引必须有条目"
    for e in entries:
        assert "](" in e and " - " in e, f"条目缺少 description：{e}"
    assert "修改 AGR0048 第 7.2 条质保期限。" in text


def test_grouped_by_type(rt, bundle):
    text = (bundle / "domains" / "equipment" / "index.md").read_text(encoding="utf-8")
    assert "## Equipment" in text and "## Knowledge Concept" in text


def test_regeneration_is_idempotent(rt, bundle):
    before = {p: p.read_bytes() for p in bundle.rglob("index.md")}
    rt.ingest()
    assert {p: p.read_bytes() for p in bundle.rglob("index.md")} == before


def test_manual_edit_is_overwritten(rt, bundle):
    idx = bundle / "domains" / "contracts" / "index.md"
    idx.write_text("# 我手改的内容\n", encoding="utf-8")
    rt.ingest()
    text = idx.read_text(encoding="utf-8")
    assert "我手改的内容" not in text
    assert GENERATED_MARK in text


def test_missing_index_does_not_break_ingest(rt, bundle):
    """上游 SPEC：consumers MUST NOT reject a bundle because of missing index.md。"""
    for p in list(bundle.rglob("index.md")):
        p.unlink()
    rep = rt.ingest()
    assert rep.concepts == len(list(bundle.rglob("concepts/*.md")))
    assert (bundle / "index.md").exists(), "ingest 应重新生成"


def test_add_document_does_not_duplicate_entries(rt, bundle, tmp_path):
    from unistile.ingest_new import add_document

    src = tmp_path / "新规程.md"
    src.write_text("# 新规程\n\n## 1. 范围\n\n适用于 A-1007。\n", encoding="utf-8")
    add_document(bundle, src, uid="kn:spec:P-001", title="新规程", domain="equipment",
                 description="临时规程。")
    rt.ingest()
    rt.ingest()
    text = (bundle / "domains" / "equipment" / "index.md").read_text(encoding="utf-8")
    assert text.count("concepts/P-001.md") == 1
