"""unistile validate：Concept 写入 Catalog 前的门禁。

L0/L1/L2 检查 + 值级往返检查。未通过 L0+L1 的 Concept 不允许进 Catalog，
否则 Catalog 会积累无法解析身份的记录。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from . import profile_v1 as profile
from . import schema_registry as registry
from . import uid as uidmod


@dataclass(frozen=True)
class Finding:
    level: str  # error | warning
    code: str
    message: str
    path: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.code}  {self.path}\n         {self.message}"


@dataclass
class Report:
    findings: list[Finding]
    checked: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _err(code: str, msg: str, path: str) -> Finding:
    return Finding("error", code, msg, path)


def _warn(code: str, msg: str, path: str) -> Finding:
    return Finding("warning", code, msg, path)


def validate_file(path: str | Path, *, bundle_root: str | Path | None = None) -> list[Finding]:
    p = Path(path)
    sp = str(p)
    out: list[Finding] = []
    text = p.read_text(encoding="utf-8")

    try:
        data, body = profile.split_frontmatter(text)
    except profile.ProfileError as e:
        return [_err("L0.parse", str(e), sp)]

    # --- L0 必填与取值 ---
    for k in profile.REQUIRED_L0:
        if not data.get(k):
            out.append(_err("L0.required", f"缺少必填字段 `{k}`", sp))
    if data.get("status") and data["status"] not in profile.STATUS_VALUES:
        out.append(
            _err("L0.status", f"status={data['status']!r} 不在 {sorted(profile.STATUS_VALUES)}", sp)
        )

    # --- L1 必填与取值 ---
    for k in profile.REQUIRED_L1:
        if not data.get(k):
            out.append(_err("L1.required", f"缺少必填字段 `{k}`", sp))
    ec = data.get("evidence_class")
    if ec and ec not in profile.EVIDENCE_CLASSES:
        out.append(
            _err("L1.evidence_class", f"evidence_class={ec!r} 不在 {sorted(profile.EVIDENCE_CLASSES)}", sp)
        )

    rev = data.get("resource_revision")
    if rev is not None and (not isinstance(rev, int) or rev < 1):
        out.append(_err("L1.resource_revision", f"resource_revision 必须是 >=1 的整数，得到 {rev!r}", sp))

    # --- 禁止字段：后端信息不得进入 Canonical Concept ---
    for k in profile.FORBIDDEN_KEYS:
        if k in data:
            out.append(
                _err(
                    "L2.forbidden",
                    f"字段 `{k}` 属于运行时 Binding（resource_bindings 表），不得写入 Concept",
                    sp,
                )
            )

    # --- uid 语法 + namespace 登记 ---
    raw_uid = data.get("uid")
    if raw_uid:
        try:
            parsed = uidmod.parse(str(raw_uid))
        except uidmod.UidError as e:
            out.append(_err("L1.uid_syntax", str(e), sp))
        else:
            if not registry.is_registered_namespace(parsed.namespace):
                out.append(
                    _err(
                        "L1.uid_namespace",
                        f"namespace `{parsed.namespace}` 未在 Schema Registry 登记"
                        f"（已登记：{sorted(registry.NAMESPACES)}）",
                        sp,
                    )
                )

    # --- aliases 形状 ---
    # 写成标量会被 list() 拆成单字（`aliases: 马旺` → ['马','旺']），静默进 Catalog，
    # 然后 resolve 的 alias 那一级永远命中不了。这种失败没有任何症状，必须在这里拦。
    if "aliases" in data:
        al = data["aliases"]
        if not isinstance(al, list):
            out.append(_err("L1.aliases_shape", f"aliases 必须是列表，得到 {type(al).__name__}：{al!r}", sp))
        else:
            for a in al:
                if not isinstance(a, str) or not a.strip():
                    out.append(_err("L1.aliases_shape", f"aliases 条目必须是非空字符串：{a!r}", sp))

    # --- relations 类型登记 ---
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            out.append(_err("L1.relation_shape", f"relations 条目不是映射：{r!r}", sp))
            continue
        rt = r.get("type")
        if not registry.is_registered_relation(str(rt)):
            out.append(
                _err("L1.relation_type", f"关系类型 `{rt}` 未登记，不得驱动导航", sp)
            )
        if "metadata" in r and not isinstance(r["metadata"], dict):
            out.append(_err("L1.relation_metadata", f"relations.metadata 必须是映射：{r['metadata']!r}", sp))
        tgt = str(r.get("target", ""))
        if tgt and not uidmod.is_valid(tgt):
            out.append(_err("L1.relation_target", f"relations.target 不是合法 uid：{tgt!r}", sp))

    # --- resource 可达 + sha256 一致 ---
    if bundle_root and data.get("resource"):
        try:
            c = profile.from_frontmatter(data, okf_path=sp, body=body)
        except profile.ProfileError as e:
            out.append(_err("L1.shape", str(e), sp))
        else:
            rp = profile.resolve_resource(c, bundle_root)
            if rp is None:
                out.append(_warn("L1.resource_scheme", f"无法解析 resource URI：{c.resource}", sp))
            elif not rp.exists():
                out.append(_err("L1.resource_missing", f"resource 不存在：{rp}", sp))
            elif c.sha256:
                actual = profile.sha256_file(rp)
                if actual != c.sha256:
                    out.append(
                        _err(
                            "L1.sha256_mismatch",
                            f"sha256 与 resource 实际字节不符\n         声明={c.sha256}\n         实际={actual}",
                            sp,
                        )
                    )
            else:
                out.append(_warn("L1.sha256_missing", "document Concept 建议声明 sha256（更新乐观锁依赖它）", sp))

    # --- 值级往返：解析→序列化→再解析 必须等价 ---
    try:
        again = yaml.safe_load(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        if again != data:
            out.append(_err("L0.roundtrip", "frontmatter 值级往返不等价（存在不可无损序列化的值）", sp))
    except Exception as e:  # noqa: BLE001
        out.append(_err("L0.roundtrip", f"往返失败：{e}", sp))

    return out


def iter_concept_files(bundle_root: str | Path) -> Iterable[Path]:
    root = Path(bundle_root)
    for p in sorted((root / "domains").rglob("*.md")):
        if p.name != "index.md":
            yield p


def validate_bundle(bundle_root: str | Path) -> Report:
    root = Path(bundle_root)
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    n = 0

    for p in iter_concept_files(root):
        n += 1
        fs = validate_file(p, bundle_root=root)
        findings.extend(fs)
        try:
            data, _ = profile.split_frontmatter(p.read_text(encoding="utf-8"))
        except profile.ProfileError:
            continue
        u = str(data.get("uid", ""))
        if u:
            if u in seen:
                findings.append(
                    _err("L2.uid_unique", f"uid 重复：{u}，已用于 {seen[u]}", str(p))
                )
            else:
                seen[u] = str(p)

    # 投影定义
    pdir = root / "projections"
    if pdir.exists():
        from ..projections import ProjectionError, load_definition

        seen_pids: dict[str, str] = {}
        for f in sorted(pdir.glob("*.yaml")):
            try:
                meta, flat = load_definition(f)
            except ProjectionError as e:
                findings.append(_err("L2.projection", str(e), str(f)))
                continue
            except Exception as e:  # noqa: BLE001
                findings.append(_err("L2.projection_parse", f"投影定义解析失败：{e}", str(f)))
                continue
            pid = meta["projection_id"]
            if pid in seen_pids:
                findings.append(_err("L2.projection_dup", f"projection_id 重复：{pid}（已用于 {seen_pids[pid]}）", str(f)))
            seen_pids[pid] = str(f)
            ids = {node.node_id for node in flat}
            for node in flat:   # 不要用 n —— 它是外层的 Concept 计数器
                if node.parent_node_id and node.parent_node_id not in ids:
                    findings.append(_err("L2.projection_parent",
                                         f"{node.node_id} 的父节点不存在：{node.parent_node_id}", str(f)))
                if node.concept_uid and node.concept_uid not in seen:
                    findings.append(_err("L2.projection_ref",
                                         f"{node.node_id} 引用了不存在的 Concept：{node.concept_uid}", str(f)))

    # 叶子索引规模
    for idx in sorted(root.rglob("index.md")):
        links = idx.read_text(encoding="utf-8").count("](")
        if links > 200:
            findings.append(
                _warn("L2.index_size", f"索引条目 {links} 条，超过 200 的建议上限", str(idx))
            )

    return Report(findings, checked=n)
