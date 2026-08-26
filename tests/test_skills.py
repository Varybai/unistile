"""Skill 格式与规范。

技能是给别的 harness 用的公开接口 —— 目录名对不上 name、引用了不存在的子命令、
或者 reference 文件丢了，安装方看不到任何报错，只会得到一个不触发或乱跑的技能。
所以这些必须由测试守住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_KEYS = {"name", "description", "license", "metadata",
                "allowed-tools", "disable-model-invocation", "version"}
SUBCOMMANDS = {"add", "validate", "ingest", "providers", "bindings",
               "tree", "where", "outline", "ask", "turn", "--help"}

DIRS = sorted(d for d in SKILLS.iterdir() if d.is_dir())


def _parts(d: Path) -> tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", (d / "SKILL.md").read_text(encoding="utf-8"), re.S)
    assert m, f"{d.name}: 缺 YAML frontmatter"
    return m.group(1), (d / "SKILL.md").read_text(encoding="utf-8")[m.end():]


def test_there_are_skills():
    assert DIRS, "skills/ 下没有技能"


@pytest.mark.parametrize("d", DIRS, ids=lambda d: d.name)
def test_frontmatter(d):
    fm, _ = _parts(d)
    name = re.search(r"^name:\s*(\S+)", fm, re.M)
    desc = re.search(r"^description:\s*(.+)$", fm, re.M)
    assert name and desc, f"{d.name}: 缺 name 或 description"
    assert NAME_RE.match(name.group(1)), f"{d.name}: name 必须是 kebab-case"
    assert len(name.group(1)) <= 64
    assert name.group(1) == d.name, "name 必须与目录名一致，否则装进 harness 后找不到"
    assert len(desc.group(1)) <= 1024
    assert "Use when" in desc.group(1), "description 必须写清什么时候触发"
    extra = set(re.findall(r"^([a-z-]+):", fm, re.M)) - ALLOWED_KEYS
    assert not extra, f"{d.name}: frontmatter 有未知键 {sorted(extra)}"


@pytest.mark.parametrize("d", DIRS, ids=lambda d: d.name)
def test_body(d):
    _, body = _parts(d)
    assert body.lstrip().startswith(f"# {d.name}")
    assert len(body.splitlines()) <= 500, "正文太长 —— 细节应该挪进 reference/ 按需加载"


@pytest.mark.parametrize("d", DIRS, ids=lambda d: d.name)
def test_bundled_references_exist(d):
    _, body = _parts(d)
    refs = set(re.findall(r"`(reference/[^`]+)`", body))
    assert refs, f"{d.name}: 没有 reference —— 细节都塞正文里了？"
    for ref in refs:
        assert (d / ref).exists(), f"{d.name}: 引用了不存在的 {ref}"


@pytest.mark.parametrize("d", DIRS, ids=lambda d: d.name)
def test_only_real_subcommands_are_referenced(d):
    _, body = _parts(d)
    used = set(re.findall(r"^unistile (\w[\w-]*)", body, re.M))
    assert used <= SUBCOMMANDS, f"{d.name}: 引用了不存在的子命令 {sorted(used - SUBCOMMANDS)}"


def test_documented_subcommands_really_exist_in_the_cli(capsys):
    """别让技能和 CLI 各改各的：技能里出现的每个子命令都必须能跑通 --help。"""
    from unistile.cli import main

    documented = set()
    for d in DIRS:
        _, body = _parts(d)
        documented |= set(re.findall(r"^unistile (\w[\w-]*)", body, re.M))
    documented.discard("--help")
    assert documented, "技能里一个子命令都没提到？"

    for sub in sorted(documented):
        with pytest.raises(SystemExit) as e:
            main([sub, "--help"])
        assert e.value.code == 0, f"unistile {sub} --help 失败 —— 技能引用了不存在的子命令"
