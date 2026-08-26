"""归一化文本平面（Normalized Text Plane）。

跨 Provider 的 locator 基准。所有 Provider 在这个平面之上建索引，切块策略可以各不相同，
但 char offset 回到同一坐标系。因此：
  1. 换 Provider 后历史 Evidence 仍可回读；
  2. 两个 Provider 的召回结果可以直接比较（对照评测的前提）；
  3. fetch_slice 由 Runtime 执行，不依赖任何 Provider 存活。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import anydoc_extract

EXTRACTOR_VERSION = "normalizer-v1"

# 原生：本来就是纯文本，直接进平面，不经任何抽取器
NATIVE_MEDIA_TYPES = frozenset({"text/markdown", "text/plain"})
# anydoc：docx/pptx/xlsx/odf/rtf/epub/csv/pdf → GFM Markdown → 同一个平面
SUPPORTED_MEDIA_TYPES = NATIVE_MEDIA_TYPES | anydoc_extract.SUPPORTED_MEDIA_TYPES


def extractor_version_for(media_type: str) -> str:
    """换抽取器就是换坐标系，版本号必须体现在 locator 上，否则偏移会静默漂移。"""
    if media_type in NATIVE_MEDIA_TYPES:
        return EXTRACTOR_VERSION
    return f"anydoc-{anydoc_extract.VERSION}+{EXTRACTOR_VERSION}"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)


class UnsupportedMediaType(ValueError):
    pass


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    level: int
    start: int
    end: int


@dataclass(frozen=True)
class Page:
    number: int
    start: int
    end: int


@dataclass
class NormalizedText:
    resource_uri: str
    revision: int
    text: str
    pages: list[Page] = field(default_factory=list)
    outline: list[Section] = field(default_factory=list)
    extractor_version: str = EXTRACTOR_VERSION

    @property
    def sha256(self) -> str:
        return text_sha256(self.text)

    def page_of(self, offset: int) -> int | None:
        for p in self.pages:
            if p.start <= offset < p.end:
                return p.number
        return None

    def section_of(self, offset: int) -> tuple[str, ...]:
        best: tuple[str, ...] = ()
        best_level = -1
        for s in self.outline:
            if s.start <= offset < s.end and s.level > best_level:
                best, best_level = s.path, s.level
        return best


def text_sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(path: str | Path, *, resource_uri: str, revision: int, media_type: str) -> NormalizedText:
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise UnsupportedMediaType(
            f"不支持 {media_type}；已支持：{sorted(SUPPORTED_MEDIA_TYPES)}。"
            " 能力缺失必须显式暴露，不得静默降级。"
        )
    if media_type in NATIVE_MEDIA_TYPES:
        text = Path(path).read_text(encoding="utf-8")
        # 单一无分页文本流，"第 1 页"是精确描述而非猜测
        pages = [Page(number=1, start=0, end=len(text))]
        outline = _markdown_outline(text) if media_type == "text/markdown" else []
    else:
        text = anydoc_extract.to_markdown(path)
        # anydoc 不保留页边界。原文确实有页，谎称 page=1 比不给更糟。
        pages = []
        outline = _markdown_outline(text)
    return NormalizedText(
        resource_uri=resource_uri, revision=revision, text=text,
        pages=pages, outline=outline,
        extractor_version=extractor_version_for(media_type),
    )


def _markdown_outline(text: str) -> list[Section]:
    heads = [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING_RE.finditer(text)]
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for i, (start, level, title) in enumerate(heads):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = len(text)
        for nstart, nlevel, _ in heads[i + 1 :]:
            if nlevel <= level:
                end = nstart
                break
        sections.append(Section(path=tuple(t for _, t in stack), level=level, start=start, end=end))
    return sections


class ResourceStore:
    """不可变 revision 的归一化文本存储。删掉整个目录可由原始 Resource 重建。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, normalized_sha: str) -> Path:
        return self.root / normalized_sha.replace("sha256:", "")

    def put(self, norm: NormalizedText) -> str:
        d = self._dir(norm.sha256)
        d.mkdir(parents=True, exist_ok=True)
        (d / "text.txt").write_text(norm.text, encoding="utf-8")
        (d / "meta.json").write_text(
            json.dumps(
                {
                    "resource_uri": norm.resource_uri,
                    "revision": norm.revision,
                    "extractor_version": norm.extractor_version,
                    "normalized_text_sha256": norm.sha256,
                    "pages": [p.__dict__ for p in norm.pages],
                    "outline": [
                        {"path": list(s.path), "level": s.level, "start": s.start, "end": s.end}
                        for s in norm.outline
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return norm.sha256

    def get_meta(self, normalized_sha: str) -> dict:
        p = self._dir(normalized_sha) / "meta.json"
        if not p.exists():
            raise FileNotFoundError(f"归一化元数据不存在：{normalized_sha}（需重新 ingest）")
        return json.loads(p.read_text(encoding="utf-8"))

    def get_text(self, normalized_sha: str) -> str:
        p = self._dir(normalized_sha) / "text.txt"
        if not p.exists():
            raise FileNotFoundError(f"归一化文本不存在：{normalized_sha}（需重新 ingest）")
        return p.read_text(encoding="utf-8")

    def read_slice(self, normalized_sha: str, start: int, end: int) -> str:
        text = self.get_text(normalized_sha)
        if not (0 <= start <= end <= len(text)):
            raise ValueError(f"越界的 char span [{start},{end})，文本长度 {len(text)}")
        return text[start:end]
