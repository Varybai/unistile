"""路由：(domain, evidence_class, media_type) → provider_id。

"局部 RAG 热插拔"的实际含义 —— 不是全局二选一，而是按维度路由，
允许不同知识域用不同后端。改路由是运行时配置，不动任何 Concept 文件。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..resources.normalizer import SUPPORTED_MEDIA_TYPES
from .errors import UnsupportedCapability


@dataclass(frozen=True)
class RouteRule:
    provider_id: str
    domain: str | None = None
    evidence_class: str | None = None
    media_type: str | None = None

    def matches(self, domain: str | None, evidence_class: str, media_type: str | None) -> bool:
        return (
            (self.domain is None or self.domain == domain)
            and (self.evidence_class is None or self.evidence_class == evidence_class)
            and (self.media_type is None or self.media_type == media_type)
        )


# 归一化之后全部落在同一个 Markdown 文本平面上，因此文档证据后端覆盖所有
# normalizer 能处理的 media_type。表以 normalizer 的能力为准，不手抄。
DEFAULT_RULES: tuple[RouteRule, ...] = tuple(
    RouteRule("local-fts", evidence_class="document", media_type=mt)
    for mt in sorted(SUPPORTED_MEDIA_TYPES)
)


class Router:
    def __init__(self, rules: tuple[RouteRule, ...] = DEFAULT_RULES):
        self.rules = rules

    def route(self, *, evidence_class: str, media_type: str | None, domain: str | None = None) -> str:
        for r in self.rules:
            if r.matches(domain, evidence_class, media_type):
                return r.provider_id
        raise UnsupportedCapability(
            f"没有 Provider 覆盖 (domain={domain}, evidence_class={evidence_class}, media_type={media_type})；"
            " 相关 Obligation 应判为 blocked，不得静默降级到其他范围"
        )

    def try_route(self, *, evidence_class: str, media_type: str | None, domain: str | None = None) -> str | None:
        try:
            return self.route(evidence_class=evidence_class, media_type=media_type, domain=domain)
        except UnsupportedCapability:
            return None
