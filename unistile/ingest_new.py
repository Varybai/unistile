"""unistile add —— 把一份新文档纳入知识库。

顺序是固定的：先落原始 Resource，再生成 Canonical Concept，最后才 ingest 建索引。
sha256 由工具计算，不允许手填 —— 它是更新乐观锁和 Binding 一致性校验的依据。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .resources import anydoc_extract
from .spec import profile_v1 as profile
from .spec import uid as uidmod
from .spec.schema_registry import is_registered_namespace, is_registered_relation
from .spec.validator import validate_file

# 原生纯文本 + anydoc 覆盖的一切。表以 anydoc 的能力为准，不手抄。
NATIVE_BY_SUFFIX = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
MEDIA_BY_SUFFIX = {**NATIVE_BY_SUFFIX, **anydoc_extract.MEDIA_TYPES}


class AddError(ValueError):
    pass


@dataclass
class AddResult:
    concept_path: Path
    resource_path: Path
    uid: str
    sha256: str


def _slug(u: uidmod.Uid) -> str:
    return f"{u.local_id}-{u.qualifier}" if u.qualifier else u.local_id


def add_document(
    bundle_root: str | Path,
    source_file: str | Path,
    *,
    uid: str,
    title: str,
    domain: str,
    concept_type: str = "Knowledge Concept",
    description: str | None = None,
    revision: int = 1,
    status: str = "stable",
    relations: list[tuple[str, str]] | None = None,
    tags: list[str] | None = None,
    external_id: str | None = None,
    aliases: list[str] | None = None,
) -> AddResult:
    root = Path(bundle_root)
    src = Path(source_file)
    if not src.exists():
        raise AddError(f"文件不存在：{src}")

    media_type = MEDIA_BY_SUFFIX.get(src.suffix.lower())
    if media_type is None:
        raise AddError(
            f"不支持的后缀 {src.suffix}；当前支持 {sorted(MEDIA_BY_SUFFIX)}。"
            " 能力缺失必须显式暴露，不要先塞进来再说。"
        )

    try:
        u = uidmod.parse(uid)   # 语法错在这里就挡住
    except uidmod.UidError as e:
        raise AddError(str(e)) from e
    if not is_registered_namespace(u.namespace):
        raise AddError(f"namespace `{u.namespace}` 未在 Schema Registry 登记；先改 schema_registry.py 并发布新版本")
    for rt, target in relations or []:
        if not is_registered_relation(rt):
            raise AddError(f"关系类型 `{rt}` 未登记，不得驱动导航")
        if not uidmod.is_valid(target):
            raise AddError(f"关系 target 不是合法 uid：{target}")

    # 1. 落原始 Resource（真相源之一）
    assets = root / "assets" / "documents"
    assets.mkdir(parents=True, exist_ok=True)
    dst = assets / src.name
    if dst.resolve() != src.resolve():
        shutil.copy2(src, dst)
    sha = profile.sha256_file(dst)

    # 2. 生成 Canonical Concept（不含任何后端信息）
    cdir = root / "domains" / domain / "concepts"
    cdir.mkdir(parents=True, exist_ok=True)
    cpath = cdir / f"{_slug(u)}.md"
    if cpath.exists():
        raise AddError(f"Concept 已存在：{cpath}；更新走 replace_resource 流程，不要覆盖写")

    lines = [
        "---",
        f'type: "{concept_type}"',
        f'title: "{title}"',
    ]
    if description:
        lines.append(f'description: "{description}"')
    lines += [
        f'resource: "asset://documents/{dst.name}"',
        f"tags: [{', '.join(tags or [])}]",
        f"status: {status}",
        "generated:",
        '  by: "tool:unistile-add"',
        f'  at: "{_utc_now()}"',
        "sources:",
        f'  - title: "{dst.name}"',
        f'    url: "asset://documents/{dst.name}"',
        f'uid: "{uid}"',
    ]
    if external_id:
        lines.append(f'external_id: "{external_id}"')
    if aliases:
        # 走 json.dumps 而不是手拼：别名里带引号/逗号时手拼会生成非法 YAML，
        # 而 JSON 数组本来就是合法的 YAML flow sequence。
        lines.append(f"aliases: {json.dumps(list(aliases), ensure_ascii=False)}")
    lines += [
        f'sha256: "{sha}"',
        f"resource_revision: {revision}",
        "evidence_class: document",
        f'media_type: "{media_type}"',
    ]
    if relations:
        lines.append("relations:")
        for rt, target in relations:
            lines += [f"  - type: {rt}", f'    target: "{target}"']
    lines += ["---", "", f"# {title}", "", "Concept Head：稳定身份与治理信息。正文证据在 resource 中，由 Provider 检索。", ""]
    cpath.write_text("\n".join(lines), encoding="utf-8")

    # 3. 领域索引不在这里维护 —— index.md 由 ingest 从 Catalog 整体重建（OKF SPEC §8）

    # 4. 门禁：不通过就回滚 Concept，不留半成品
    findings = validate_file(cpath, bundle_root=root)
    errors = [f for f in findings if f.level == "error"]
    if errors:
        cpath.unlink()
        raise AddError("生成的 Concept 未通过校验（已回滚）：\n" + "\n".join(str(f) for f in errors))

    return AddResult(cpath, dst, uid, sha)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
