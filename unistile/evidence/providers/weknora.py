"""weknora/v0 —— 只有能力声明，没有检索实现。

"保留接口"不等于写一个空类。这里交付的是四件可验收物之一：**能力声明**。
它现在就有用：Runtime 可以据此验证"遇到 stochastic、不可回读的 Provider 时降级逻辑
是否正确"，不需要真实后端在场。

证据边界（E2）：以下能力来自固定快照 f20427df133d37b32bccdcc948f631c69ad55e68 的
静态源码分析，**未运行验证**，因此 provider_version 标为 0-unverified。接入时必须重测。

接入前的四个待办（见 OKF 方案篇 P3）：
  1. SSE knowledge_references → RankedEvidenceCandidate 映射
  2. /agent-chat session 生命周期管理
  3. scope_binding_ids → WeKnora knowledge_ids 编译
  4. chunk 文本 → 归一化文本平面的偏移对齐；对齐失败则 locator 降级为 provider_opaque，
     证据等级封顶 derived-chunk，需要 original-resource 的 obligation 必须判 blocked
"""

from __future__ import annotations

from ..contract import (
    BindRequest,
    BindResult,
    ContextExpandRequest,
    EvidenceSearchRequest,
    EvidenceSearchResult,
    HealthReport,
    ProviderCapabilities,
)
from ..errors import NotImplementedProvider

PROVIDER_ID = "weknora"
PROVIDER_VERSION = "0-unverified"


class WeKnoraProvider:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=PROVIDER_ID,
            provider_version=PROVIDER_VERSION,
            query_expansion="agentic",              # /agent-chat 内部 knowledge_search，1–5 个语义查询
            retrieval_modes=("hybrid",),            # 内部 Vector + Keyword/BM25 + RRF
            rerank=True,
            diversity="mmr",
            parent_child_expansion=True,
            neighbor_expansion=True,
            image_understanding="ocr_caption",      # VLM 产出文本后参与文本检索，非原生图像向量
            locator_kinds=("chunk_id", "page", "section_path"),
            supports_slice_readback=False,          # 偏移对齐达标后才可置 True
            determinism="stochastic",
            max_scope_size=64,
            max_rounds=4,
            media_types=("application/pdf", "text/markdown", "text/plain", "image/png", "image/jpeg"),
        )

    def bind(self, req: BindRequest) -> BindResult:
        raise NotImplementedProvider("weknora.bind：P3 接入，需先实现 knowledge_id 编译")

    def reindex(self, binding_id: str) -> BindResult:
        raise NotImplementedProvider("weknora.reindex：P3 接入")

    def unbind(self, binding_id: str) -> None:
        raise NotImplementedProvider("weknora.unbind：P3 接入")

    def search(self, req: EvidenceSearchRequest) -> EvidenceSearchResult:
        raise NotImplementedProvider("weknora.search：P3 接入，需先实现 SSE → candidate 映射与偏移对齐")

    def expand(self, req: ContextExpandRequest) -> EvidenceSearchResult:
        raise NotImplementedProvider("weknora.expand：P3 接入")

    def health(self) -> HealthReport:
        return HealthReport(PROVIDER_ID, False, "POC 阶段默认关闭；仅登记 capabilities()")
