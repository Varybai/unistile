"""C9 Golden Set —— 换 Provider 时的对照基准。

core 用例必须全中；known_limitation 用例记录 keyword-only Provider 的能力边界，
只报告不断言。接入向量/混合 Provider 后，这些用例应当翻转 —— 那正是对照评测的信号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unistile.app import Runtime

GOLDEN = json.loads((Path(__file__).parent / "golden_set.json").read_text(encoding="utf-8"))
K = 3


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    import shutil

    d = tmp_path_factory.mktemp("golden")
    shutil.copytree("knowledge", d / "knowledge")
    r = Runtime(d / "knowledge", d / "runtime")
    r.ingest()
    yield r
    r.close()


def _hit(rt, case) -> bool:
    b = rt.adapter.search(concept_uids=case["scope"], query=case["query"])
    for e in b.evidence[:K]:
        if e.concept_uid == case["expect_concept"] and case["expect_section"] in e.locator.section_path:
            return True
    return False


@pytest.mark.parametrize("case", [c for c in GOLDEN["cases"] if "known_limitation" not in c],
                         ids=lambda c: c["id"])
def test_core_recall_at_k(rt, case):
    assert _hit(rt, case), f"{case['id']} 未命中 {case['expect_section']}"


def test_locator_readback_on_golden(rt):
    from unistile.resources.normalizer import text_sha256

    for case in GOLDEN["cases"]:
        for e in rt.adapter.search(concept_uids=case["scope"], query=case["query"]).evidence:
            got = rt.adapter.fetch_slice(e.locator)
            assert text_sha256(got) == e.locator.content_sha256


def test_report_capability_boundary(rt, capsys):
    core = [c for c in GOLDEN["cases"] if "known_limitation" not in c]
    lim = [c for c in GOLDEN["cases"] if "known_limitation" in c]
    core_hits = sum(_hit(rt, c) for c in core)
    lim_hits = sum(_hit(rt, c) for c in lim)
    with capsys.disabled():
        print(f"\n  golden(local-fts)  core recall@{K} = {core_hits}/{len(core)}"
              f"   known-limitation 命中 = {lim_hits}/{len(lim)}")
        for c in lim:
            print(f"    · {c['id']} {c['query']!r}: {'HIT（可更新 golden set）' if _hit(rt, c) else 'miss'}"
                  f" — {c['known_limitation']}")
    assert core_hits == len(core)
