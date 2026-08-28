"""Profile 校验的门禁行为：坏 Concept 必须在进 Catalog 之前被挡住。"""

from __future__ import annotations

import pytest

from unistile.spec import uid as uidmod
from unistile.spec.validator import validate_bundle, validate_file

BASE = """---
type: "Agreement"
title: "测试协议"
status: stable
uid: "kn:agreement:TEST-1"
evidence_class: document
---

正文
"""


def _write(tmp_path, text, name="c.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_demo_bundle_passes(tmp_path):
    from pathlib import Path

    expected = len([p for p in Path("knowledge/domains").rglob("*.md") if p.name != "index.md"])
    r = validate_bundle("knowledge")
    assert r.ok, "\n".join(str(f) for f in r.errors)
    assert r.checked == expected


def test_backend_fields_are_rejected(tmp_path):
    bad = BASE.replace("evidence_class: document", "evidence_class: document\naccess:\n  method: weknora\n  binding_key: wk-1")
    codes = {f.code for f in validate_file(_write(tmp_path, bad))}
    assert "L2.forbidden" in codes


@pytest.mark.parametrize("field", ["provider_id", "knowledge_id", "indexed_sha256", "embedding_model"])
def test_each_forbidden_field_rejected(tmp_path, field):
    bad = BASE.replace("evidence_class: document", f"evidence_class: document\n{field}: x")
    codes = {f.code for f in validate_file(_write(tmp_path, bad, f"{field}.md"))}
    assert "L2.forbidden" in codes


def test_unregistered_uid_namespace_rejected(tmp_path):
    bad = BASE.replace("kn:agreement:TEST-1", "kn:equipment-a:acceptance-spec")
    codes = {f.code for f in validate_file(_write(tmp_path, bad))}
    assert "L1.uid_namespace" in codes


def test_malformed_uid_rejected(tmp_path):
    bad = BASE.replace('"kn:agreement:TEST-1"', '"AGR0048"')
    codes = {f.code for f in validate_file(_write(tmp_path, bad))}
    assert "L1.uid_syntax" in codes


def test_relation_metadata_must_be_mapping(tmp_path):
    bad = BASE.replace("evidence_class: document",
                       'evidence_class: document\nrelations:\n  - type: amends\n'
                       '    target: "kn:agreement:AGR0048"\n    metadata: "7.2"')
    codes = {f.code for f in validate_file(_write(tmp_path, bad, "relmeta.md"))}
    assert "L1.relation_metadata" in codes


def test_scalar_aliases_rejected(tmp_path):
    """`aliases: 马旺` 会被 list() 拆成 ['马','旺']，静默进 Catalog 后 alias 解析永远落空。

    这种失败没有任何症状 —— 只能在写入之前拦。
    """
    bad = BASE.replace("evidence_class: document", "evidence_class: document\naliases: 马旺")
    codes = {f.code for f in validate_file(_write(tmp_path, bad, "alias-scalar.md"))}
    assert "L1.aliases_shape" in codes


@pytest.mark.parametrize("entry", ['""', "123", "null", '"   "'])
def test_alias_entries_must_be_nonempty_strings(tmp_path, entry):
    bad = BASE.replace("evidence_class: document", f"evidence_class: document\naliases: [{entry}]")
    codes = {f.code for f in validate_file(_write(tmp_path, bad, "alias-entry.md"))}
    assert "L1.aliases_shape" in codes


def test_empty_alias_list_is_fine(tmp_path):
    """没有别名是正常的，不是错误。"""
    ok = BASE.replace("evidence_class: document", "evidence_class: document\naliases: []")
    codes = {f.code for f in validate_file(_write(tmp_path, ok, "alias-empty.md"))}
    assert "L1.aliases_shape" not in codes


def test_valid_aliases_accepted(tmp_path):
    ok = BASE.replace("evidence_class: document",
                      'evidence_class: document\naliases: ["主设备采购协议", "AGR0048"]')
    codes = {f.code for f in validate_file(_write(tmp_path, ok, "alias-ok.md"))}
    assert "L1.aliases_shape" not in codes


def test_unregistered_relation_rejected(tmp_path):
    bad = BASE.replace("evidence_class: document",
                       "evidence_class: document\nrelations:\n  - type: 修订\n    target: \"kn:agreement:AGR0048\"")
    codes = {f.code for f in validate_file(_write(tmp_path, bad))}
    assert "L1.relation_type" in codes


def test_missing_required_fields(tmp_path):
    bad = BASE.replace("evidence_class: document\n", "")
    codes = {f.code for f in validate_file(_write(tmp_path, bad))}
    assert "L1.required" in codes


def test_sha256_mismatch_detected(tmp_path):
    (tmp_path / "assets" / "documents").mkdir(parents=True)
    (tmp_path / "assets" / "documents" / "d.md").write_text("真实内容", encoding="utf-8")
    bad = BASE.replace(
        "evidence_class: document",
        'evidence_class: document\nresource: "asset://documents/d.md"\nsha256: "sha256:deadbeef"',
    )
    codes = {f.code for f in validate_file(_write(tmp_path, bad), bundle_root=tmp_path)}
    assert "L1.sha256_mismatch" in codes


def test_uid_parse_roundtrip():
    u = uidmod.parse("kn:equipment:A-1007:acceptance-spec")
    assert (u.namespace, u.local_id, u.qualifier) == ("equipment", "A-1007", "acceptance-spec")
    assert str(u) == "kn:equipment:A-1007:acceptance-spec"
    assert str(u.parent) == "kn:equipment:A-1007"
