"""index.md 自动生成 —— 与上游 OKF SPEC §8 对齐。

上游规则（GoogleCloudPlatform/knowledge-catalog/okf/SPEC.md §8）：
  - index.md 可选，缺失不得导致 bundle 被拒；producer MAY 自动生成
  - 只有 bundle 根的 index.md 可带 frontmatter，且只放 okf_version
  - body 由若干 heading 分组，条目格式：`* [Title](relative-url) - description`
  - 条目 SHOULD 带上被链 Concept frontmatter 里的 description

因此 index.md 在本实现中是**派生物**：由 Catalog 重建，不手写。
手改会在下次 ingest 时被覆盖 —— 导航结构的真相在 Concept 自身（domain + type），
不在这个文件里。
"""

from __future__ import annotations

from pathlib import Path

OKF_VERSION = "0.2"
GENERATED_MARK = "<!-- 由 unistile ingest 自动生成（OKF SPEC §8）；手改会被覆盖 -->"


def _entry(title: str, url: str, description: str | None) -> str:
    line = f"* [{title}]({url})"
    return f"{line} - {description}" if description else line


def write_domain_index(bundle_root: str | Path, domain: str, rows: list[dict]) -> Path:
    """rows: [{uid,title,description,okf_path,type}]，按 type 分组。"""
    path = Path(bundle_root) / "domains" / domain / "index.md"
    groups: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["type"], r["uid"])):
        groups.setdefault(r["type"], []).append(r)

    out = [f"# {domain}", "", GENERATED_MARK, ""]
    for gtype, items in groups.items():
        out += [f"## {gtype}", ""]
        for r in items:
            rel = Path(r["okf_path"]).name
            out.append(_entry(r["title"], f"concepts/{rel}", r.get("description")))
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return path


def write_root_index(bundle_root: str | Path, domains: dict[str, int]) -> Path:
    """根索引只列领域入口。数万对象的搜索走 Catalog，不靠这个文件。"""
    path = Path(bundle_root) / "index.md"
    out = [
        "---",
        f'okf_version: "{OKF_VERSION}"',
        "---",
        "",
        "# 知识库索引",
        "",
        GENERATED_MARK,
        "",
        "## Domains",
        "",
    ]
    for domain, n in sorted(domains.items()):
        out.append(_entry(domain, f"domains/{domain}/index.md", f"{n} concepts"))
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return path


def rebuild(bundle_root: str | Path, catalog) -> list[Path]:
    """从 Catalog 重建根索引与全部领域索引。幂等：同样的 Catalog 产出同样的字节。"""
    rows = [
        dict(r)
        for r in catalog.db.execute(
            "SELECT uid, title, description, okf_path, type, domain FROM concepts"
            " WHERE domain IS NOT NULL ORDER BY domain, type, uid"
        ).fetchall()
    ]
    by_domain: dict[str, list[dict]] = {}
    for r in rows:
        by_domain.setdefault(r["domain"], []).append(r)

    written = [write_domain_index(bundle_root, d, items) for d, items in sorted(by_domain.items())]
    written.append(write_root_index(bundle_root, {d: len(v) for d, v in by_domain.items()}))
    return written
