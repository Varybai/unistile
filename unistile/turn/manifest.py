"""Local Navigation Manifest —— 当前这一层能看见什么，以及看不见什么。

两件事必须同时给出，否则 Agent 会把"没展示"当成"不存在"：
  1. 当前列出的少量入口，每个带覆盖提示、字符成本和预览。
  2. 未列出的区域、总数和省略原因，加上翻页 cursor。

coverage_hints 的诚实边界：
  Concept 级是确定的 —— view_node.concept_uid ∈ obligation.scope_uids 是纯集合运算。
  Section 级不确定 —— 「7. 质量保证」是否真的写了质保期，不读内容不知道。
  所以这里只声明"落在哪条义务的范围内"，不声明"命中"。
  真正的收益不是提示更准，是候选集从无限（任意查询词）收敛成有限（几个章节）。

导航数据全部来自归一化文本平面，与 Provider 无关 —— 换后端不影响这张图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .contract import EvidenceObligation, TaskContract

HINT_BASIS = "concept-in-obligation-scope"

OPEN_STATUSES = ("unseen", "candidate")


@dataclass(frozen=True)
class ChildHandle:
    view_node_id: str
    head: str
    concept_uid: str
    kind: str                                  # concept / section
    coverage_hints: tuple[str, ...] = ()
    section_path: tuple[str, ...] = ()
    char_start: int | None = None
    char_end: int | None = None
    preview: str = ""
    child_count: int = 0
    expected_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = {
            "view_node_id": self.view_node_id,
            "head": self.head,
            "kind": self.kind,
            "concept_uid": self.concept_uid,
            "coverage_hints": list(self.coverage_hints),
            "hint_basis": HINT_BASIS,
            "child_count": self.child_count,
            "expected_cost": {"tokens": self.expected_tokens, "reads": 1},
        }
        if self.section_path:
            d["section_path"] = list(self.section_path)
            d["char_span"] = [self.char_start, self.char_end]
            d["preview"] = self.preview
        return d


@dataclass(frozen=True)
class LocalNavigationManifest:
    node: str | None
    child_handles: tuple[ChildHandle, ...]
    child_count: int
    omission_summary: str
    next_cursor: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "child_handles": [h.to_dict() for h in self.child_handles],
            "child_count": self.child_count,
            "returned": len(self.child_handles),
            "omission_summary": self.omission_summary,
            "next_cursor": self.next_cursor,
        }


class ManifestError(ValueError):
    """未知 view_node_id，或该节点没有下一层。"""


# ---------- 内部：读归一化文本平面 ----------
def _outline(rt, concept_uid: str) -> tuple[list[dict], str, str]:
    """返回 (outline, text, normalized_sha)。没有 Binding 的 Concept 没有导航图。"""
    row = rt.catalog.get_concept(concept_uid)
    if row is None:
        raise ManifestError(f"未知 Concept：{concept_uid}")
    bindings = rt.catalog.bindings_for([concept_uid], role=None)
    if not bindings:
        return [], "", ""
    rev = rt.catalog.get_revision(row["resource_uri"], max(b.resource_revision for b in bindings))
    if rev is None:
        return [], "", ""
    sha = rev["normalized_text_sha256"]
    return rt.resources.get_meta(sha)["outline"], rt.resources.get_text(sha), sha


def _local(uid: str) -> str:
    return uid.split(":")[-1]


def _short_names(scope_uids: Sequence[str]) -> dict[str, str]:
    """短名好打；重名就退回完整 uid，绝不静默合并两个 Concept。"""
    counts: dict[str, int] = {}
    for uid in scope_uids:
        counts[_local(uid)] = counts.get(_local(uid), 0) + 1
    return {uid: (_local(uid) if counts[_local(uid)] == 1 else uid) for uid in scope_uids}


def _hints(obligations: Sequence[EvidenceObligation], concept_uid: str) -> tuple[str, ...]:
    return tuple(
        o.id for o in obligations
        if o.status in OPEN_STATUSES and concept_uid in o.scope_uids
    )


def _preview(text: str, s: dict, width: int = 60) -> str:
    body = text[s["start"]:s["end"]].split("\n", 1)
    return (body[1].strip().replace("\n", " ")[:width]) if len(body) > 1 else ""


def _depth_children(outline: list[dict], path: tuple[str, ...] | None) -> list[int]:
    """path=None 时返回最浅的非单点层；否则返回 path 的直接子节点。"""
    if path is None:
        for depth in (2, 1):
            idx = [i for i, s in enumerate(outline) if len(s["path"]) == depth]
            if idx:
                return idx
        return []
    n = len(path)
    return [
        i for i, s in enumerate(outline)
        if len(s["path"]) == n + 1 and tuple(s["path"][:n]) == path
    ]


# ---------- 构建 ----------
def build(
    rt,
    contract: TaskContract,
    *,
    node: str | None = None,
    limit: int = 8,
    cursor: int = 0,
) -> LocalNavigationManifest:
    handles, total, omitted_note = (
        _root_layer(rt, contract) if node is None else _section_layer(rt, contract, node)
    )
    page = handles[cursor:cursor + limit]
    shown = cursor + len(page)
    parts = []
    if shown < total:
        rest = [h.head for h in handles[shown:]][:5]
        parts.append(f"另有 {total - shown} 项未列出（{' / '.join(rest)}{' …' if total - shown > 5 else ''}）")
    if omitted_note:
        parts.append(omitted_note)
    return LocalNavigationManifest(
        node=node,
        child_handles=tuple(page),
        child_count=total,
        omission_summary="；".join(parts),
        next_cursor=shown if shown < total else None,
    )


def _root_layer(rt, contract: TaskContract) -> tuple[list[ChildHandle], int, str]:
    """根层 = scope 里每个还有未满足义务的 Concept。义务已满足的折叠进省略说明。"""
    names = _short_names(contract.scope_uids)
    handles: list[ChildHandle] = []
    settled: list[str] = []
    for uid in contract.scope_uids:
        hints = _hints(contract.obligations, uid)
        row = rt.catalog.get_concept(uid)
        title = row["title"] if row is not None else uid
        if not hints:
            settled.append(title)
            continue
        outline, text, _ = _outline(rt, uid)
        handles.append(
            ChildHandle(
                view_node_id=names[uid],
                head=title,
                concept_uid=uid,
                kind="concept",
                coverage_hints=hints,
                child_count=len(_depth_children(outline, None)),
                expected_tokens=len(text) // 2,
            )
        )
    note = f"{len(settled)} 个 Concept 的相关义务已满足，未展开（{' / '.join(settled)}）" if settled else ""
    return handles, len(handles), note


def _section_layer(rt, contract: TaskContract, node: str) -> tuple[list[ChildHandle], int, str]:
    concept_uid, idx = resolve_node(rt, contract, node)
    outline, text, _ = _outline(rt, concept_uid)
    names = _short_names(contract.scope_uids)
    short = names[concept_uid]
    parent_path = tuple(outline[idx]["path"]) if idx is not None else None

    children = _depth_children(outline, parent_path)
    if not children:
        raise ManifestError(f"{node} 没有下一层；用 turn act --view-node {node} 直接读取")

    hints = _hints(contract.obligations, concept_uid)
    handles = [
        ChildHandle(
            view_node_id=f"{short}#{i}",
            head=outline[i]["path"][-1],
            concept_uid=concept_uid,
            kind="section",
            coverage_hints=hints,
            section_path=tuple(outline[i]["path"]),
            char_start=outline[i]["start"],
            char_end=outline[i]["end"],
            preview=_preview(text, outline[i]),
            child_count=len(_depth_children(outline, tuple(outline[i]["path"]))),
            expected_tokens=(outline[i]["end"] - outline[i]["start"]) // 2,
        )
        for i in children
    ]
    return handles, len(handles), ""


def resolve_node(rt, contract: TaskContract, node: str) -> tuple[str, int | None]:
    """view_node_id → (concept_uid, outline 下标)。下标为 None 表示这是 Concept 节点。"""
    short, _, tail = node.partition("#")
    names = _short_names(contract.scope_uids)
    match = [uid for uid, s in names.items() if s == short]
    if not match:
        raise ManifestError(
            f"未知 view_node_id：{node}（本轮 scope 只有 {sorted(names.values())}）"
        )
    concept_uid = match[0]
    if not tail:
        return concept_uid, None
    if not tail.isdigit():
        raise ManifestError(f"非法 view_node_id：{node}")
    outline, _, _ = _outline(rt, concept_uid)
    i = int(tail)
    if i >= len(outline):
        raise ManifestError(f"{node} 越界：{concept_uid} 只有 {len(outline)} 个 section")
    return concept_uid, i


def handle_of(rt, contract: TaskContract, node: str) -> ChildHandle:
    """单个节点的完整 handle —— read 动作据此定位字符区间。"""
    concept_uid, idx = resolve_node(rt, contract, node)
    outline, text, _ = _outline(rt, concept_uid)
    names = _short_names(contract.scope_uids)
    hints = _hints(contract.obligations, concept_uid)
    if idx is None:
        row = rt.catalog.get_concept(concept_uid)
        return ChildHandle(
            view_node_id=names[concept_uid], head=row["title"], concept_uid=concept_uid,
            kind="concept", coverage_hints=hints,
            child_count=len(_depth_children(outline, None)), expected_tokens=len(text) // 2,
        )
    s = outline[idx]
    return ChildHandle(
        view_node_id=node, head=s["path"][-1], concept_uid=concept_uid, kind="section",
        coverage_hints=hints, section_path=tuple(s["path"]),
        char_start=s["start"], char_end=s["end"], preview=_preview(text, s),
        child_count=len(_depth_children(outline, tuple(s["path"]))),
        expected_tokens=(s["end"] - s["start"]) // 2,
    )


def read_span(rt, concept_uid: str, start: int, end: int) -> dict[str, Any]:
    """按字符区间从归一化文本平面取原文。

    不经过任何 Provider，因此与索引是否落后无关 —— stale 说的是 Provider 索引，
    这里读的是当前 revision 的文本平面本身。
    """
    row = rt.catalog.get_concept(concept_uid)
    bindings = rt.catalog.bindings_for([concept_uid], role=None)
    if not bindings:
        raise ManifestError(f"{concept_uid} 没有 Binding，无法读取")
    b = max(bindings, key=lambda x: x.resource_revision)
    rev = rt.catalog.get_revision(row["resource_uri"], b.resource_revision)
    sha = rev["normalized_text_sha256"]
    return {
        "text": rt.resources.read_slice(sha, start, end),
        "normalized_text_sha256": sha,
        "extractor_version": rev["extractor_version"],
        "resource_uri": row["resource_uri"],
        "resource_revision": b.resource_revision,
        "source_sha256": b.source_sha256,
    }
