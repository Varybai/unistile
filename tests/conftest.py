from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unistile.evidence.contract import BindRequest  # noqa: E402
from unistile.evidence.providers.local_fts import LocalFtsProvider  # noqa: E402
from unistile.evidence.providers.null_provider import NullProvider  # noqa: E402
from unistile.evidence.providers.weknora import WeKnoraProvider  # noqa: E402
from unistile.resources.normalizer import ResourceStore, normalize  # noqa: E402
from unistile.spec import profile_v1 as profile  # noqa: E402

BUNDLE = ROOT / "knowledge"

# 契约测试覆盖的 Provider。只有一个 Provider 时接口无法被证伪，因此 POC 内置两个。
PROVIDER_FACTORIES = {
    "local-fts": lambda tmp: LocalFtsProvider(tmp / "local-fts.sqlite"),
    "null": lambda tmp: NullProvider(),
}


@pytest.fixture
def bundle(tmp_path):
    """knowledge/ 的一次性副本。ingest 会重建 index.md，测试不得写仓库里的 bundle。"""
    import shutil

    dst = tmp_path / "knowledge"
    shutil.copytree(BUNDLE, dst)
    return dst


@pytest.fixture(scope="session")
def resource_store(tmp_path_factory) -> ResourceStore:
    return ResourceStore(tmp_path_factory.mktemp("resources"))


@pytest.fixture
def corpus(resource_store):
    """两份文档 → 两个 binding，用于验证 scope 隔离。"""
    out = {}
    for name, uid, rev in (
        ("AGR0048-v3.md", "kn:agreement:AGR0048", 3),
        ("Supplement-02.md", "kn:agreement:Supplement-02", 1),
    ):
        path = BUNDLE / "assets" / "documents" / name
        norm = normalize(path, resource_uri=f"asset://documents/{name}", revision=rev, media_type="text/markdown")
        resource_store.put(norm)
        out[uid] = BindRequest(
            binding_id=f"bind:{uid}:rev{rev}:test",
            concept_uid=uid,
            resource_uri=norm.resource_uri,
            resource_revision=rev,
            media_type="text/markdown",
            source_sha256=profile.sha256_file(path),
            normalized_text_sha256=norm.sha256,
            text=norm.text,
            outline=tuple((s.path, s.start, s.end) for s in norm.outline),
            pages=tuple((pg.number, pg.start, pg.end) for pg in norm.pages),
        )
    return out


@pytest.fixture(params=sorted(PROVIDER_FACTORIES))
def provider(request, tmp_path, corpus):
    p = PROVIDER_FACTORIES[request.param](tmp_path)
    for req in corpus.values():
        p.bind(req)
    return p


@pytest.fixture
def weknora():
    return WeKnoraProvider()
