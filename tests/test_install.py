"""install-skills：只往装了的 harness 写，整目录替换，源目录能被找到。"""

from __future__ import annotations

from pathlib import Path

import pytest

from unistile.cli import main
from unistile.install import (
    InstallError,
    available_skills,
    bundled_skills_dir,
    install,
    known_harnesses,
)


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)      # 装了
    (home / ".pi" / "agent").mkdir(parents=True)  # 装了
    return home                                   # 其余 harness 没装


def test_bundled_skills_dir_found():
    src = bundled_skills_dir()
    assert src.is_dir()
    assert {p.name for p in available_skills(src)} == {"unistile-answer", "unistile-author"}


def test_readme_is_not_a_skill():
    """skills/README.md 没有 SKILL.md，不该被当成技能装出去。"""
    assert "README.md" not in {p.name for p in available_skills()}


def test_only_installed_harnesses_get_written(fake_home: Path):
    results = install(home=fake_home)

    assert {r.harness for r in results} == {"Claude Code", "Pi"}
    assert all(r.status == "installed" for r in results)
    assert (fake_home / ".claude" / "skills" / "unistile-answer" / "SKILL.md").is_file()
    assert (fake_home / ".pi" / "agent" / "skills" / "unistile-author" / "SKILL.md").is_file()
    assert not (fake_home / ".codex").exists()      # 没装的 harness 不被凭空创建
    assert not (fake_home / ".cursor").exists()


def test_reference_subdirectory_comes_along(fake_home: Path):
    install(home=fake_home)
    assert (fake_home / ".claude" / "skills" / "unistile-answer" / "reference" / "packet.md").is_file()


def test_all_flag_writes_every_known_path(fake_home: Path):
    results = install(home=fake_home, all_harnesses=True)
    assert {r.harness for r in results} == {h.name for h in known_harnesses(fake_home)}


def test_reinstall_replaces_instead_of_merging(fake_home: Path):
    install(home=fake_home)
    stale = fake_home / ".claude" / "skills" / "unistile-answer" / "STALE.md"
    stale.write_text("上一版留下的文件")

    install(home=fake_home)

    assert not stale.exists(), "整目录替换，旧文件不该幸存"
    assert (stale.parent / "SKILL.md").is_file()


def test_dry_run_writes_nothing(fake_home: Path):
    results = install(home=fake_home, dry_run=True)
    assert all(r.status == "installed" for r in results)
    assert not (fake_home / ".claude" / "skills").exists()


def test_dest_overrides_detection(tmp_path: Path, fake_home: Path):
    dest = tmp_path / "somewhere"
    results = install(home=fake_home, dest=dest)

    assert len(results) == 1
    assert (dest / "unistile-answer" / "SKILL.md").is_file()
    assert not (fake_home / ".claude" / "skills").exists()


def test_empty_source_is_an_error(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InstallError):
        install(src=empty, dest=tmp_path / "out")


def test_no_harness_detected_reports_zero(tmp_path: Path):
    bare = tmp_path / "bare-home"
    bare.mkdir()
    results = install(home=bare)
    assert [r.status for r in results] == ["skipped"]


def test_cli_dest_roundtrip(tmp_path: Path, capsys):
    dest = tmp_path / "cli-dest"
    assert main(["install-skills", "--dest", str(dest)]) == 0
    assert (dest / "unistile-author" / "SKILL.md").is_file()
    assert "unistile-answer" in capsys.readouterr().out


def test_cli_dry_run_json(tmp_path: Path, capsys):
    import json

    dest = tmp_path / "nope"
    assert main(["install-skills", "--dest", str(dest), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["detail"] == "dry-run"
    assert not dest.exists()
