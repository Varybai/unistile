"""anydoc 抽取层：docx/pptx/xlsx/odf/rtf/epub/csv/pdf → GFM → 同一个文本平面。

要证明三件事：
  1. 非 Markdown 文档也能拿到 outline、char offset 和精确回读；
  2. extractor_version 变了 —— 换抽取器就是换坐标系，必须体现在 locator 上；
  3. anydoc 给不了页边界，就不许填 page。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.docxgen import MAINTENANCE_BLOCKS, write_docx  # noqa: E402

from unistile.app import Runtime  # noqa: E402
from unistile.evidence.routing import Router  # noqa: E402
from unistile.ingest_new import MEDIA_BY_SUFFIX, add_document  # noqa: E402
from unistile.resources import anydoc_extract as ax  # noqa: E402
from unistile.resources.normalizer import (  # noqa: E402
    NATIVE_MEDIA_TYPES,
    SUPPORTED_MEDIA_TYPES,
    UnsupportedMediaType,
    extractor_version_for,
    normalize,
    text_sha256,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def docx(tmp_path):
    return write_docx(tmp_path / "维护协议.docx", MAINTENANCE_BLOCKS)


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "台账.csv"
    p.write_text("设备编号,额定功率,质保期\nA-1007,75 kW,12 个月\n", encoding="utf-8")
    return p


# ---------- 抽取 ----------
def test_docx_headings_become_markdown_outline(docx):
    n = normalize(docx, resource_uri="asset://d.docx", revision=1, media_type=DOCX)
    heads = [s.path[-1] for s in n.outline]
    assert heads == ["A-1007 维护服务协议（第 1 版）", "1. 响应时限", "2. 备件供应", "3. 服务费用"]
    assert n.outline[1].level == 2


def test_offsets_land_on_the_stored_text(docx):
    n = normalize(docx, resource_uri="asset://d.docx", revision=1, media_type=DOCX)
    s = next(x for x in n.outline if x.path[-1] == "1. 响应时限")
    assert "4 小时内响应" in n.text[s.start:s.end]


def test_csv_becomes_a_markdown_table(csv_file):
    n = normalize(csv_file, resource_uri="asset://t.csv", revision=1, media_type="text/csv")
    assert "| 设备编号 |" in n.text and "A-1007" in n.text


# ---------- 坐标系身份 ----------
def test_extractor_version_records_anydoc(docx):
    n = normalize(docx, resource_uri="asset://d.docx", revision=1, media_type=DOCX)
    assert n.extractor_version == f"anydoc-{ax.VERSION}+normalizer-v1"


def test_native_text_keeps_its_own_extractor_version():
    """md/txt 本来就是纯文本，不经 anydoc —— 老 locator 不受影响。"""
    assert extractor_version_for("text/markdown") == "normalizer-v1"
    assert all(extractor_version_for(m) != "normalizer-v1"
               for m in SUPPORTED_MEDIA_TYPES - NATIVE_MEDIA_TYPES)


# ---------- 页边界：不知道就不填 ----------
def test_anydoc_resources_claim_no_pages(docx):
    n = normalize(docx, resource_uri="asset://d.docx", revision=1, media_type=DOCX)
    assert n.pages == [] and n.page_of(0) is None


def test_native_text_is_a_single_exact_page(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# T\n\n正文", encoding="utf-8")
    n = normalize(p, resource_uri="asset://a.md", revision=1, media_type="text/markdown")
    assert n.page_of(0) == 1


# ---------- 错误分类 ----------
def test_unknown_media_type_is_refused():
    with pytest.raises(UnsupportedMediaType, match="不支持"):
        normalize("x.bin", resource_uri="asset://x", revision=1, media_type="application/x-binary")


def test_broken_docx_fails_loudly_not_silently(tmp_path):
    p = tmp_path / "坏的.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("nothing.txt", "not a docx")
    with pytest.raises(ax.ExtractionFailed):
        normalize(p, resource_uri="asset://b.docx", revision=1, media_type=DOCX)


def test_a_broken_document_does_not_fail_the_whole_bundle(tmp_path, bundle):
    """一份抽不出来 != 整个 bundle 失败；跳过并留下原因。"""
    bad = tmp_path / "坏的.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("nothing.txt", "x")
    r = Runtime(bundle, tmp_path / "runtime")
    add_document(bundle, bad, uid="kn:spec:broken", title="坏文件", domain="equipment")
    rep = r.ingest()
    assert any("kn:spec:broken" == uid and "ExtractionFailed" in why for uid, why in rep.skipped)
    assert rep.bound >= 1        # 其余文档照常入库
    r.close()


# ---------- 装配 ----------
def test_every_supported_media_type_has_a_route():
    router = Router()
    for mt in SUPPORTED_MEDIA_TYPES:
        assert router.try_route(evidence_class="document", media_type=mt) == "local-fts"


def test_suffix_table_covers_everything_anydoc_supports():
    assert set(ax.MEDIA_TYPES).issubset(MEDIA_BY_SUFFIX)
    assert ".pdf" in MEDIA_BY_SUFFIX and ".docx" in MEDIA_BY_SUFFIX


# ---------- 端到端 ----------
def test_docx_end_to_end_is_original_resource(tmp_path, bundle, docx):
    r = Runtime(bundle, tmp_path / "runtime")
    add_document(bundle, docx, uid="kn:equipment:A-1007:svc", title="维护服务协议",
                 domain="equipment")
    r.ingest()
    b = r.adapter.search(concept_uids=["kn:equipment:A-1007:svc"], query="安全库存")
    assert b.evidence and b.max_level() == "original-resource"
    e = b.evidence[0]
    assert e.locator.page is None                       # 不冒充页码
    assert e.locator.extractor_version.startswith("anydoc-")
    assert text_sha256(r.adapter.fetch_slice(e.locator)) == e.locator.content_sha256
    r.close()
