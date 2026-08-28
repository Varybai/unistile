"""身份解析：姓名/别名 → uid，以及「库里没有这个实体」这个拒答出口。

这一层守的是门禁**之前**的那一步。事故（OMP 会话 01a03cfd）里查一个不存在的人名，
agent 因为没有正式入口，转头直接查 runtime 的 sqlite，整轮门禁就此绕过。
所以 resolve 必须做到两件事：命中要准，落空要**拒绝**而不是含糊地报个用法错误。
"""

from __future__ import annotations

import json

import pytest

from unistile.app import Runtime
from unistile.catalog.store import CatalogStore
from unistile.cli import main
from unistile.ingest_new import add_document

AGR = "kn:agreement:AGR0048"


@pytest.fixture
def rt(tmp_path, bundle):
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    yield r
    r.close()


# ---------- resolve 的四级优先级 ----------
def test_resolves_by_uid(rt):
    assert [r["uid"] for r in rt.catalog.resolve(AGR)] == [AGR]


def test_resolves_by_alias(rt):
    """AGR0048 的 frontmatter 里 aliases 含「主设备采购协议」。"""
    assert [r["uid"] for r in rt.catalog.resolve("主设备采购协议")] == [AGR]


def test_resolves_by_title_substring(rt):
    assert AGR in [r["uid"] for r in rt.catalog.resolve("AGR0048")]


def test_resolves_by_external_id(tmp_path, bundle):
    """external_id 这一级优先于 alias 和 title。"""
    add_document(
        bundle, bundle / "assets" / "documents" / "AGR0048-v3.md",
        uid="kn:agreement:EXT-TEST", title="外部编号测试", domain="contracts",
        description="external_id 解析用", external_id="HR-9981",
    )
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    try:
        assert [row["uid"] for row in r.catalog.resolve("HR-9981")] == ["kn:agreement:EXT-TEST"]
    finally:
        r.close()


def test_unknown_entity_resolves_to_nothing(rt):
    assert rt.catalog.resolve("马旺") == []


# ---------- LIKE 元字符必须转义 ----------
@pytest.mark.parametrize("wildcard", ["%", "_", "%%"])
def test_like_wildcards_do_not_match_everything(rt, wildcard):
    """不转义时 resolve('%') 会返回全表 —— 正是这段代码声称禁止的全库无界检索。"""
    assert rt.catalog.resolve(wildcard) == []


def test_wildcard_still_matches_a_literal_occurrence(tmp_path, bundle):
    """转义是为了让 % 变成字面量，不是为了让它永远查不到。"""
    add_document(
        bundle, bundle / "assets" / "documents" / "AGR0048-v3.md",
        uid="kn:spec:discount", title="折扣 50% 说明", domain="contracts",
        description="字面百分号",
    )
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    try:
        assert [row["uid"] for row in r.catalog.resolve("50%")] == ["kn:spec:discount"]
    finally:
        r.close()


# ---------- 近似候选：确定性，且不越界到文本平面 ----------
def test_near_matches_finds_the_close_alias(rt):
    near = rt.catalog.near_matches("主设备采购协定")     # 「议」写成了「定」
    assert [n["uid"] for n in near][:1] == [AGR]
    assert near[0]["matched_on"] == "alias"


def test_near_matches_is_deterministic(rt):
    a = rt.catalog.near_matches("主设备采购协定")
    b = rt.catalog.near_matches("主设备采购协定")
    assert a == b, "同库同输入必须同输出 —— 这是字符相似度，不是语义相似度"


def test_near_matches_ignores_body_text(rt):
    """「质保期」在 AGR0048 正文里，但不在任何身份字段上。

    近似候选只看 Catalog 身份字段。在这里搜正文等于把无界检索从后门放回来。
    """
    assert rt.catalog.near_matches("质保期") == []


def test_near_matches_respects_threshold_and_limit(rt):
    assert rt.catalog.near_matches("完全无关的一串字", threshold=0.99) == []
    assert len(rt.catalog.near_matches("AGR", threshold=0.1, limit=2)) <= 2


def test_near_matches_on_empty_query(rt):
    assert rt.catalog.near_matches("   ") == []


# ---------- CLI 契约：命中 0，落空 3 ----------
def _args(bundle, tmp_path):
    return ["--bundle", str(bundle), "--runtime", str(tmp_path / "runtime")]


def test_cli_resolve_hit_exits_zero(rt, bundle, tmp_path, capsys):
    assert main([*_args(bundle, tmp_path), "resolve", "主设备采购协议"]) == 0
    assert AGR in capsys.readouterr().out


def test_cli_resolve_miss_exits_three_with_near_misses(rt, bundle, tmp_path, capsys):
    code = main([*_args(bundle, tmp_path), "resolve", "主设备采购协定", "--json"])
    assert code == 3, "定位不到是拒答（3），不是用法错误（2）"

    payload = json.loads(capsys.readouterr().out)
    assert payload["stop_reason"] == "entity_not_in_catalog"
    assert payload["resolved"] == []
    assert [n["uid"] for n in payload["near_misses"]][:1] == [AGR]


def test_cli_resolve_unknown_entity_has_no_near_misses(rt, bundle, tmp_path, capsys):
    assert main([*_args(bundle, tmp_path), "resolve", "马旺", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["near_misses"] == []


def test_cli_ask_without_concept_refuses_the_same_way(rt, bundle, tmp_path, capsys):
    """ask 是无门禁的检索，但「拒绝做无界检索」和 resolve 是同一个语义。"""
    assert main([*_args(bundle, tmp_path), "ask", "马旺", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "entity_not_in_catalog"


# ---------- 授权路径：--alias 让不可读的 title 也能被寻址 ----------
def test_alias_written_by_add_document_is_resolvable(tmp_path, bundle):
    """事故 bundle 的 title 全是 UUID。没有 --alias 就没有任何办法定位到它。"""
    uuid_title = "HRBoost 候选人简历 64f41f68 / 300dab39"
    add_document(
        bundle, bundle / "assets" / "documents" / "AGR0048-v3.md",
        uid="kn:concept:64f41f68", title=uuid_title, domain="contracts",
        description="标题不可读，靠 alias 寻址", aliases=["马德旺"],
    )
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    try:
        assert [row["uid"] for row in r.catalog.resolve("马德旺")] == ["kn:concept:64f41f68"]
    finally:
        r.close()


def test_alias_with_quotes_survives_the_frontmatter_roundtrip(tmp_path, bundle):
    """手拼 YAML 会在这里炸。add_document 用 json.dumps 输出 flow sequence。"""
    add_document(
        bundle, bundle / "assets" / "documents" / "AGR0048-v3.md",
        uid="kn:agreement:QUOTED", title="引号测试", domain="contracts",
        description="别名里带引号和逗号", aliases=['他说"好"', "A, B"],
    )
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    try:
        assert [row["uid"] for row in r.catalog.resolve('他说"好"')] == ["kn:agreement:QUOTED"]
        assert [row["uid"] for row in r.catalog.resolve("A, B")] == ["kn:agreement:QUOTED"]
    finally:
        r.close()


def test_catalog_store_can_be_used_standalone(tmp_path):
    """near_matches 不依赖 Runtime，空库不该炸。"""
    store = CatalogStore(tmp_path / "catalog.sqlite")
    try:
        assert store.near_matches("任何东西") == []
        assert store.resolve("任何东西") == []
    finally:
        store.close()


# ---------- 整句提问：仍然拒绝，但候选必须诚实 ----------
def test_near_matches_survives_being_wrapped_in_a_question(tmp_path, bundle):
    """「马旺的资料是什么？」vs 别名「马德旺」整串只有 0.33，会被问句长度稀释到阈值以下。

    没有滑窗的话，一个明明在库里的人会被报成「没有近似候选」—— 那是在撒谎，
    比不给候选更糟。这正是事故里 agent 转头自己去 grep 的那个岔路口。
    """
    add_document(
        bundle, bundle / "assets" / "documents" / "AGR0048-v3.md",
        uid="kn:concept:64f41f68", title="HRBoost 候选人简历 64f41f68 / 300dab39",
        domain="contracts", description="标题不可读", aliases=["马德旺"],
    )
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    try:
        near = r.catalog.near_matches("马旺的资料是什么？")
        assert [n["matched_value"] for n in near][:1] == ["马德旺"]
    finally:
        r.close()


def test_exact_name_inside_a_question_scores_full(tmp_path, bundle):
    add_document(
        bundle, bundle / "assets" / "documents" / "AGR0048-v3.md",
        uid="kn:concept:64f41f68", title="HRBoost 候选人简历 64f41f68 / 300dab39",
        domain="contracts", description="标题不可读", aliases=["马德旺"],
    )
    r = Runtime(bundle, tmp_path / "runtime")
    r.ingest()
    try:
        near = r.catalog.near_matches("马德旺的教育背景是什么？")
        assert near[0]["matched_value"] == "马德旺" and near[0]["ratio"] == 1.0
    finally:
        r.close()


@pytest.mark.parametrize("query", ["质保期是多久", "完全无关的问题", "这份合同的付款方式是什么？"])
def test_ordinary_questions_produce_no_candidates(rt, query):
    """滑窗不能把候选变成噪声 —— 每次都返回一堆无关 Concept 就等于没有信号。"""
    assert rt.catalog.near_matches(query) == []


def test_very_long_query_does_not_slide(rt):
    """超长输入不是在问某个实体，别在上面滑窗烧 CPU。"""
    assert rt.catalog.near_matches("质保 " * 300) == []
