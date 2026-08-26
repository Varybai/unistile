"""把打包好的 Skill 装到各个 Agent Harness 的 skills 目录。

Harness 的发现路径各不相同，但都读同一种 Agent Skills 格式（`<name>/SKILL.md`
带 YAML frontmatter），所以一份技能可以原样铺到所有 harness。

只往「已经装了这个 harness」的地方写：marker 目录不存在就跳过，不主动
创建整个 harness 的家目录。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


class InstallError(RuntimeError):
    """技能源目录找不到，或目标目录写不进去。"""


@dataclass(frozen=True)
class Harness:
    name: str
    marker: Path      # 这个目录存在 == harness 装了
    skills: Path      # 用户级 skills 目录（可能还不存在，会创建）
    note: str = ""


def known_harnesses(home: Path | None = None) -> tuple[Harness, ...]:
    h = home or Path.home()
    return (
        Harness("Claude Code", h / ".claude", h / ".claude" / "skills"),
        Harness("Codex", h / ".codex", h / ".codex" / "skills"),
        Harness("Cursor", h / ".cursor", h / ".cursor" / "skills"),
        Harness("Grok", h / ".grok", h / ".grok" / "skills", "也会读 ~/.claude/skills"),
        Harness("Pi", h / ".pi" / "agent", h / ".pi" / "agent" / "skills"),
        Harness("OMP", h / ".omp" / "agent", h / ".omp" / "agent" / "skills"),
        Harness("Windsurf", h / ".windsurf", h / ".windsurf" / "skills"),
    )


def bundled_skills_dir() -> Path:
    """技能源目录：装过的包在 unistile/_skills，源码 checkout 在仓库根 skills/。"""
    pkg = Path(__file__).resolve().parent
    for candidate in (pkg / "_skills", pkg.parent / "skills"):
        if candidate.is_dir():
            return candidate
    raise InstallError(
        "找不到技能源目录。装的是 sdist 或非常规布局的话，"
        "请从源码仓库运行：git clone https://github.com/Varybai/unistile.git"
    )


def available_skills(src: Path | None = None) -> tuple[Path, ...]:
    """源目录下每个含 SKILL.md 的子目录就是一个技能（README.md 之类不算）。"""
    root = src or bundled_skills_dir()
    return tuple(sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file()))


@dataclass(frozen=True)
class Result:
    harness: str
    target: Path
    installed: tuple[str, ...]
    status: str       # "installed" | "skipped" | "failed"
    detail: str = ""


def install(
    *,
    home: Path | None = None,
    src: Path | None = None,
    dest: Path | None = None,
    all_harnesses: bool = False,
    dry_run: bool = False,
) -> tuple[Result, ...]:
    """把每个技能铺到每个目标目录。整目录替换，不做增量合并。

    dest 给了就只装那一个目录；否则遍历 known_harnesses()，
    all_harnesses=False 时跳过 marker 不存在的（== 没装这个 harness）。
    """
    skills = available_skills(src)
    if not skills:
        raise InstallError(f"技能源目录里没有任何 SKILL.md：{src or bundled_skills_dir()}")

    if dest is not None:
        targets = [Harness("<--dest>", dest, dest)]
    else:
        targets = [
            hn for hn in known_harnesses(home)
            if all_harnesses or hn.marker.is_dir()
        ]

    results: list[Result] = []
    for hn in targets:
        if dry_run:
            results.append(Result(hn.name, hn.skills, tuple(s.name for s in skills), "installed", "dry-run"))
            continue
        try:
            hn.skills.mkdir(parents=True, exist_ok=True)
            for skill in skills:
                target = hn.skills / skill.name
                if target.is_symlink():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
                shutil.copytree(skill, target)
        except OSError as exc:
            results.append(Result(hn.name, hn.skills, (), "failed", str(exc)))
            continue
        results.append(Result(hn.name, hn.skills, tuple(s.name for s in skills), "installed", hn.note))

    if not results:
        results.append(Result("(none)", Path(), (), "skipped", "没有检测到已安装的 harness"))
    return tuple(results)
