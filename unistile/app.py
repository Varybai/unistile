"""Runtime 装配：Catalog + ResourceStore + Provider Registry + Router + Adapter。

切换后端 = 改 routing 规则或 binding 的 role，不动任何 Concept 文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog.store import CatalogStore
from .evidence.adapter import DocumentEvidenceAdapter
from .evidence.errors import UnsupportedCapability
from .evidence.providers.local_fts import LocalFtsProvider
from .evidence.providers.null_provider import NullProvider
from .evidence.providers.weknora import WeKnoraProvider
from .evidence.registry import ProviderRegistry
from .evidence.routing import Router
from .indexgen import rebuild as rebuild_indexes
from .projections import rebuild as rebuild_projections
from .evidence.contract import BindRequest
from .resources.anydoc_extract import ExtractionFailed
from .resources.normalizer import ResourceStore, UnsupportedMediaType, normalize
from .spec import profile_v1 as profile
from .spec.validator import validate_bundle

DEFAULT_RUNTIME_DIR = "runtime"


@dataclass
class IngestReport:
    concepts: int = 0
    bound: int = 0
    skipped: list[tuple[str, str]] = None  # (uid, reason)
    indexes: list[str] = None
    projections: dict = None

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []
        if self.indexes is None:
            self.indexes = []
        if self.projections is None:
            self.projections = {}


class Runtime:
    def __init__(self, bundle_root: str | Path, runtime_dir: str | Path = DEFAULT_RUNTIME_DIR):
        self.bundle_root = Path(bundle_root)
        self.runtime_dir = Path(runtime_dir)
        self.catalog = CatalogStore(self.runtime_dir / "catalog.sqlite")
        self.resources = ResourceStore(self.runtime_dir / "resources")
        self.registry = ProviderRegistry()
        self.registry.register(LocalFtsProvider(self.runtime_dir / "indexes" / "local-fts.sqlite"))
        self.registry.register(NullProvider())
        # 已登记能力声明，但 POC 期默认关闭 —— capabilities() 仍可被读取与测试
        self.registry.register(WeKnoraProvider(), enabled=False)
        self.router = Router()
        self.adapter = DocumentEvidenceAdapter(self.catalog, self.registry, self.resources)

    def close(self) -> None:
        self.catalog.close()

    # ---------- ingest ----------
    def ingest(self, *, strict: bool = True) -> IngestReport:
        report = validate_bundle(self.bundle_root)
        if strict and not report.ok:
            raise ValueError(
                "Concept 未通过 L0+L1 校验，拒绝写入 Catalog：\n"
                + "\n".join(str(f) for f in report.errors)
            )

        out = IngestReport()
        for path in sorted((self.bundle_root / "domains").rglob("*.md")):
            if path.name == "index.md":
                continue
            c = profile.load_concept(path)
            domain = path.relative_to(self.bundle_root / "domains").parts[0]
            self.catalog.upsert_concept(c, domain=domain)
            out.concepts += 1

            provider_id = self.router.try_route(
                evidence_class=c.evidence_class, media_type=c.media_type, domain=domain
            )
            if provider_id is None:
                out.skipped.append((c.uid, f"无 Provider 覆盖 evidence_class={c.evidence_class}"))
                continue
            rp = profile.resolve_resource(c, self.bundle_root)
            if rp is None or not rp.exists():
                out.skipped.append((c.uid, "resource 不可解析"))
                continue

            try:
                norm = normalize(
                    rp,
                    resource_uri=c.resource or "",
                    revision=c.resource_revision,
                    media_type=c.media_type or "text/plain",
                )
            except (UnsupportedMediaType, ExtractionFailed) as e:
                # 这一份抽不出来 != 整个 bundle 失败；跳过并留下原因，对应 Obligation 会 blocked
                out.skipped.append((c.uid, f"{type(e).__name__}: {e}"))
                continue

            self.resources.put(norm)
            self.catalog.upsert_revision(
                norm.resource_uri, norm.revision, c.sha256 or "", norm.sha256, norm.extractor_version
            )

            binding_id = f"bind:{c.uid}:rev{c.resource_revision}:{provider_id}"
            provider = self.registry.get(provider_id)
            res = provider.bind(
                BindRequest(
                    binding_id=binding_id,
                    concept_uid=c.uid,
                    resource_uri=norm.resource_uri,
                    resource_revision=norm.revision,
                    media_type=c.media_type or "text/plain",
                    source_sha256=c.sha256 or "",
                    normalized_text_sha256=norm.sha256,
                    text=norm.text,
                    outline=tuple((s.path, s.start, s.end) for s in norm.outline),
                    pages=tuple((p.number, p.start, p.end) for p in norm.pages),
                    extractor_version=norm.extractor_version,
                )
            )
            self.catalog.upsert_binding(
                binding_id=binding_id,
                concept_uid=c.uid,
                resource_uri=norm.resource_uri,
                resource_revision=norm.revision,
                provider_id=provider_id,
                provider_version=provider.capabilities().provider_version,
                backend_object_id=res.backend_object_id,
                source_sha256=c.sha256 or "",
                indexed_sha256=res.indexed_sha256,
                status=res.status,
                role="primary",
            )
            out.bound += 1

        # index.md 是派生物：每次 ingest 由 Catalog 重建（上游 OKF SPEC §8 允许自动生成）
        out.indexes = [str(p) for p in rebuild_indexes(self.bundle_root, self.catalog)]
        out.projections = rebuild_projections(self.bundle_root, self.catalog)
        return out

    # ---------- 有界 hop：只沿已登记的治理关系 ----------
    def expand_scope(self, seed_uids: list[str], *, relation_types=("amends", "supersedes")) -> list[str]:
        scope = list(dict.fromkeys(seed_uids))
        for uid in list(scope):
            for e in self.catalog.edges_to(uid, list(relation_types)):
                if e["source_uid"] not in scope:
                    scope.append(e["source_uid"])
        return scope
