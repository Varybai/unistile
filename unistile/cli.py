"""unistile —— 证据门禁运行时命令行。

  unistile add <文件> --uid ... --title ... --domain ...   纳入一份新文档
  unistile validate                    仅校验 OKF Bundle
  unistile ingest                      校验 → 归一化 → Catalog → Provider bind
  unistile providers                   列出已注册 Provider 及其能力声明
  unistile bindings                    列出 Binding（含 role / stale 状态）
  unistile resolve "<姓名/别名>"        身份解析 → uid；库里没有这个实体则 exit 3
  unistile install-skills              把技能铺到各 Agent Harness 的 skills 目录
  unistile tree [projection] [--node ID]  逐层导航（多投影，含 omission 统计）
  unistile where <concept_uid>         这个 Concept 出现在哪些投影下
  unistile outline <concept_uid>       文档的 section 导航图（不检索，Provider 无关）
  unistile ask "<问题>" --concept UID  受控范围内检索 + 回读校验 + Evidence Envelope

一轮问答（有状态，义务由 Runtime 派生，Agent 删不掉）：
  unistile turn start "<问题>"          开轮：派生义务 + 定 scope + 算预算
  unistile turn act <turn_id> --obligation OBL   执行一次证据检索，更新义务状态
  unistile turn answer <turn_id> --claim "…"     过门禁才输出；不过 exit 3
  unistile turn abstain <turn_id> --reason "…"   明确拒答，也是合法出口
  unistile turn show <turn_id>          当前 packet；不给 id 则列出所有轮次
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import Runtime
from .ingest_new import AddError, add_document
from .install import InstallError, bundled_skills_dir
from .install import install as install_skills
from .projections import children as projection_children
from .projections import projections_of
from .envelope import bundle_to_envelope
from .evidence.contract import SearchBudget
from .evidence.errors import ProviderError
from .spec.validator import validate_bundle
from .turn.contract import BudgetLedger, ScopeResolutionError, TurnError
from .turn.driver import run as auto_run
from .turn.manifest import ManifestError
from .turn.session import TurnSession


def _rt(args) -> Runtime:
    return Runtime(args.bundle, args.runtime)


def cmd_add(args) -> int:
    rels = []
    for r in args.relation or []:
        if ":" not in r:
            print(f"--relation 格式应为 <type>:<target_uid>，得到 {r!r}", file=sys.stderr)
            return 2
        rt, target = r.split(":", 1)
        rels.append((rt, target))
    try:
        res = add_document(
            args.bundle, args.file, uid=args.uid, title=args.title, domain=args.domain,
            concept_type=args.type, description=args.description, revision=args.revision,
            relations=rels, tags=args.tag or [], external_id=args.external_id,
            aliases=args.alias or [],
        )
    except AddError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"resource : {res.resource_path}")
    print(f"concept  : {res.concept_path}")
    print(f"sha256   : {res.sha256}")
    if args.no_ingest:
        print("\n未建索引（--no-ingest）。执行 `unistile ingest` 后才可被检索。")
        return 0
    rt = _rt(args)
    rep = rt.ingest()
    rt.close()
    print(f"\ningest: concepts={rep.concepts}  bound={rep.bound}")
    return 0


def _print_unresolved(query: str, near: list, *, as_json: bool) -> int:
    """身份解析失败的统一出口。exit 3 —— 这是拒答，不是用法错误。

    `near_misses` 是 Catalog 身份字段上的字符相似度候选，给人确认用；
    调用方不许拿它自动改写查询，那等于把「查错人」变成静默的事实错误。
    """
    if as_json:
        print(json.dumps({
            "query": query,
            "resolved": [],
            "near_misses": near,
            "stop_reason": "entity_not_in_catalog",
        }, ensure_ascii=False, indent=2))
        return 3
    print(f"无法定位 Concept：{query!r}（不允许全库无界检索）", file=sys.stderr)
    if near:
        print("\n近似候选（字符相似度，非语义匹配 —— 请人工确认是不是同一个）：", file=sys.stderr)
        for n in near:
            print(f"  {n['uid']}\n    {n['matched_on']}={n['matched_value']!r}  ratio={n['ratio']}",
                  file=sys.stderr)
    else:
        print("没有近似候选。这个实体大概率不在库里。", file=sys.stderr)
    return 3


def cmd_resolve(args) -> int:
    """姓名/别名 → uid。给 Agent 一个正式入口，替代直接翻 runtime 下的 sqlite。"""
    rt = _rt(args)
    rows = rt.catalog.resolve(args.query, limit=args.limit)
    if not rows:
        near = rt.catalog.near_matches(args.query, limit=args.limit)
        rt.close()
        return _print_unresolved(args.query, near, as_json=args.json)

    resolved = [
        {"uid": r["uid"], "title": r["title"], "status": r["status"],
         "evidence_class": r["evidence_class"], "domain": r["domain"]}
        for r in rows
    ]
    rt.close()
    if args.json:
        print(json.dumps({"query": args.query, "resolved": resolved, "near_misses": []},
                         ensure_ascii=False, indent=2))
        return 0
    for r in resolved:
        print(f"{r['uid']}\n  {r['title']}   [{r['status']} / {r['evidence_class']}]")
    print(f"\n{len(resolved)} 个。用 `turn start \"<问题>\" --concept <uid>` 开轮。")
    return 0


def cmd_validate(args) -> int:
    r = validate_bundle(args.bundle)
    for f in r.findings:
        print(f)
    print(f"\nchecked={r.checked}  errors={len(r.errors)}  warnings={len(r.findings) - len(r.errors)}")
    print("OK" if r.ok else "FAILED")
    return 0 if r.ok else 1


def cmd_ingest(args) -> int:
    rt = _rt(args)
    try:
        rep = rt.ingest()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"concepts={rep.concepts}  bound={rep.bound}  index.md={len(rep.indexes)}"
          f"  projections={sum(rep.projections.values())} nodes in {len(rep.projections)}")
    for uid, reason in rep.skipped:
        print(f"  skipped {uid}: {reason}")
    rt.close()
    return 0


def cmd_providers(args) -> int:
    rt = _rt(args)
    for pid in rt.registry.ids():
        caps = rt.registry.capabilities(pid)
        state = "enabled" if rt.registry.is_enabled(pid) else "disabled"
        print(f"\n{pid} ({caps.provider_version}) [{state}]")
        print(f"  max_evidence_level : {caps.max_evidence_level}")
        print(f"  retrieval_modes    : {list(caps.retrieval_modes) or '—'}")
        print(f"  query_expansion    : {caps.query_expansion}   rerank={caps.rerank}  diversity={caps.diversity}")
        print(f"  locator_kinds      : {list(caps.locator_kinds) or '—'}  readback={caps.supports_slice_readback}")
        print(f"  determinism        : {caps.determinism}   max_rounds={caps.max_rounds}")
    rt.close()
    return 0


def cmd_bindings(args) -> int:
    rt = _rt(args)
    rows = rt.catalog.db.execute(
        "SELECT binding_id, provider_id, role, status, resource_revision, indexed_sha256, source_sha256"
        " FROM resource_bindings ORDER BY concept_uid, provider_id"
    ).fetchall()
    for r in rows:
        stale = "STALE" if r["indexed_sha256"] != r["source_sha256"] else "fresh"
        print(f"{r['role']:<8} {r['status']:<8} {stale:<6} {r['provider_id']:<10} rev{r['resource_revision']}  {r['binding_id']}")
    print(f"\n{len(rows)} bindings")
    rt.close()
    return 0


def cmd_tree(args) -> int:
    rt = _rt(args)
    if not args.projection:
        print("可用投影：")
        for r in rt.catalog.db.execute("SELECT * FROM projections ORDER BY projection_id"):
            n = rt.catalog.db.execute(
                "SELECT count(*) FROM projection_nodes WHERE projection_id=?", (r["projection_id"],)
            ).fetchone()[0]
            print(f"  {r['projection_id']:<22} {r['kind']:<13} {n:>3} 节点   {r['title']}")
        rt.close()
        return 0

    rows, total = projection_children(
        rt.catalog, args.projection, args.node, limit=args.limit, offset=args.offset
    )
    if total == 0 and args.node:
        print(f"{args.node} 没有子节点（叶子）")
        rt.close()
        return 0
    head = args.node or "(根)"
    print(f"{args.projection} / {head}")
    for r in rows:
        kid = rt.catalog.db.execute(
            "SELECT count(*) FROM projection_nodes WHERE projection_id=? AND parent_node_id=?",
            (args.projection, r["node_id"]),
        ).fetchone()[0]
        mark = "▸" if kid else "·"
        tail = f"  [{kid} 项]" if kid else ""
        print(f"  {mark} {r['label']}{tail}")
        print(f"      node={r['node_id']}")
        if r["concept_uid"]:
            desc = f"   {r['description']}" if r["description"] else ""
            print(f"      → {r['concept_uid']}  ({r['status']}/{r['evidence_class']}){desc}")
        if r["view_metadata"]:
            print(f"      meta={r['view_metadata']}")
    shown = len(rows)
    if shown < total:
        print(f"\n  已列出 {shown}/{total}，其余 {total - shown} 项未展开（--offset {args.offset + shown} 继续）")
    else:
        print(f"\n  共 {total} 项，已全部列出")
    rt.close()
    return 0


def cmd_where(args) -> int:
    rt = _rt(args)
    row = rt.catalog.get_concept(args.concept_uid)
    if row is None:
        print(f"未知 Concept：{args.concept_uid}", file=sys.stderr)
        return 2
    print(f"{row['title']}  ({args.concept_uid})")
    print("Canonical 文件只有一份：", row["okf_path"], "\n")
    for r in projections_of(rt.catalog, args.concept_uid):
        print(f"  [{r['projection_id']:<20}] {r['title']}")
        print(f"      node   = {r['node_id']}")
        print(f"      parent = {r['parent_node_id']}")
    rt.close()
    return 0


def cmd_outline(args) -> int:
    """Local Navigation Manifest 的最小形态：不检索就能知道文档里有哪些区域。

    数据来自归一化文本平面，与 Provider 无关 —— 换后端不影响这张导航图。
    """
    rt = _rt(args)
    row = rt.catalog.get_concept(args.concept_uid)
    if row is None:
        print(f"未知 Concept：{args.concept_uid}", file=sys.stderr)
        return 2
    revision = args.revision
    if revision is None:
        bs = rt.catalog.bindings_for([args.concept_uid], role=None)
        if not bs:
            print(f"{args.concept_uid} 没有任何 Binding；先 unistile ingest", file=sys.stderr)
            return 1
        revision = max(b.resource_revision for b in bs)
    rev = rt.catalog.get_revision(row["resource_uri"], revision)
    if rev is None:
        print(f"{args.concept_uid} 没有 revision {revision} 的归一化记录；先 unistile ingest", file=sys.stderr)
        return 1
    meta = rt.resources.get_meta(rev["normalized_text_sha256"])
    text = rt.resources.get_text(rev["normalized_text_sha256"])

    print(f"{row['title']}  ({args.concept_uid})")
    print(f"resource : {row['resource_uri']}  rev{revision}  {len(text)} chars")
    print(f"extractor: {meta['extractor_version']}   pages: {len(meta['pages'])}\n")
    for s_ in meta["outline"]:
        indent = "  " * (s_["level"] - 1)
        leaf = s_["path"][-1]
        span = s_["end"] - s_["start"]
        body = text[s_["start"]:s_["end"]].split("\n", 1)
        preview = (body[1].strip().replace("\n", " ")[:args.preview] if len(body) > 1 else "")
        print(f"{indent}{leaf}")
        print(f"{indent}  [{s_['start']}:{s_['end']}] {span} chars   {preview}")
    print(f"\n{len(meta['outline'])} sections。用 ask --concept {args.concept_uid} 在其中检索。")
    rt.close()
    return 0


def cmd_ask(args) -> int:
    rt = _rt(args)
    seeds = list(args.concept or [])
    if not seeds:
        rows = rt.catalog.resolve(args.query)
        seeds = [r["uid"] for r in rows][:3]
    if not seeds:
        near = rt.catalog.near_matches(args.query)
        rt.close()
        return _print_unresolved(args.query, near, as_json=args.json)

    scope = seeds if args.no_hop else rt.expand_scope(seeds)
    hopped = [u for u in scope if u not in seeds]
    print(f"seed  : {seeds}")
    print(f"scope : {scope}" + (f"   (+{len(hopped)} 沿 amends/supersedes 展开)" if hopped else ""))

    try:
        bundle = rt.adapter.search(
            concept_uids=scope,
            query=args.query,
            obligation_ids=tuple(args.obligation or ()),
            budget=SearchBudget(max_candidates=args.limit),
            section_prefix=tuple(args.section) if args.section else (),
        )
    except ProviderError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    env = bundle_to_envelope(bundle, query=args.query)
    if args.json:
        print(json.dumps(env, ensure_ascii=False, indent=2))
    else:
        print(f"\nmax_evidence_level = {env['max_evidence_level']}   evidence={len(bundle.evidence)}"
              f"  rejected={len(bundle.rejected)}")
        for e in bundle.evidence:
            loc = e.locator
            path = " / ".join(loc.section_path) or "(前言)"
            print(f"\n─ {e.concept_uid}  rev{e.resource_revision}  score={e.score:.3f} ({e.score_kind})")
            page = f"  p{loc.page}" if loc.page is not None else ""
            print(f"  {path}   char[{loc.char_start}:{loc.char_end}]{page}  level={e.evidence_level}")
            print(f"  {e.evidence_text.strip()[:180]}")
        for r in bundle.rejected:
            print(f"\n✗ {r.candidate_id}  {r.reason_code}: {r.reason}")
        for w in bundle.warnings:
            print(f"\n! {w.code}: {w.message}")
        for o in bundle.omissions:
            if o.get("reason"):
                print(f"\n… omission[{o['provider_id']}]: {o['reason']}")
    rt.close()
    return 0



# ---------------- turn：一轮问答 ----------------
def _print_packet(pk: dict) -> None:
    print(f"turn  : {pk['turn_id']}   status={pk['status']}")
    print(f"scope : {pk['scope_uids']}")
    print(f"budget: calls {pk['budget']['tool_calls']}  reads {pk['budget']['evidence_reads']}"
          f"  tokens {pk['budget']['tokens_available']} 可用（留 {pk['budget']['reserved']}）")
    print("\n义务：")
    mark = {"unseen": "○", "candidate": "◐", "supported": "●", "blocked": "✗"}
    for o in pk["obligations"]:
        req = "required" if o["required"] else "optional"
        print(f"  {mark[o['status']]} {o['id']}   [{o['status']} / {o['priority']} / {req}]")
        print(f"      {o['requirement']}")
        print(f"      scope={o['scope_uids']}")
        if o.get("blocked_reason"):
            print(f"      ✗ {o['blocked_reason']}")
        if o.get("evidence_ids"):
            print(f"      证据 {o['evidence_ids']}")
    m = pk.get("manifest")
    if m:
        node = m["node"] or "(根层)"
        print(f"\n导航 @ {node}   列出 {m['returned']}/{m['child_count']}")
        for h in m["child_handles"]:
            cov = ",".join(h["coverage_hints"]) or "-"
            more = f"  ▸{h['child_count']} 子节点" if h["child_count"] else ""
            print(f"  [{h['view_node_id']}] {h['head']}{more}")
            print(f"      覆盖 {cov}   ~{h['expected_cost']['tokens']} tokens")
            if h.get("preview"):
                print(f"      {h['preview']}")
        if m["omission_summary"]:
            print(f"  … {m['omission_summary']}")
        if m["next_cursor"] is not None:
            print(f"  … 下一页：--cursor {m['next_cursor']}")

    g = pk["gate"]
    print(f"\ngate  : allowed={g['allowed']}  {g['stop_reason']}")
    print(f"合法动作: {pk['legal_actions']}")
    if "answer" not in pk["legal_actions"]:
        print("          （answer 不在列表里 —— 现在物理上答不了）")


def cmd_turn_start(args) -> int:
    rt = _rt(args)
    ts = TurnSession(rt)
    try:
        budget = BudgetLedger(tool_calls=args.max_calls, evidence_reads=args.max_reads)
        state = ts.start(
            args.question,
            seeds=args.concept,
            no_hop=args.no_hop,
            budget=budget,
            allow_qualified_answer=not args.strict_answer,
        )
    except ScopeResolutionError as e:
        # 实体不在库里 —— 拒答（3），不是参数写错（2）。轮次根本没建起来，所以没有 packet。
        rt.close()
        return _print_unresolved(e.query, e.near_misses, as_json=args.json)
    except TurnError as e:
        print(str(e), file=sys.stderr)
        rt.close()
        return 2
    pk = ts.packet(state)
    print(json.dumps(pk, ensure_ascii=False, indent=2) if args.json else "", end="")
    if not args.json:
        _print_packet(pk)
    rt.close()
    return 0


def cmd_turn_act(args) -> int:
    rt = _rt(args)
    ts = TurnSession(rt)
    try:
        state = ts.load(args.turn_id)
        if args.view_node:
            pk = ts.read_view_node(state, args.obligation, args.view_node)
        else:
            pk = ts.search_document_evidence(
                state,
                args.obligation,
                query=args.query,
                limit=args.limit,
                section_prefix=tuple(args.section) if args.section else (),
            )
    except (TurnError, ProviderError, ManifestError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        rt.close()
        return 2
    print(json.dumps(pk, ensure_ascii=False, indent=2) if args.json else "", end="")
    if not args.json:
        _print_packet(pk)
    rt.close()
    return 0


def cmd_turn_answer(args) -> int:
    rt = _rt(args)
    ts = TurnSession(rt)
    try:
        state = ts.load(args.turn_id)
        out = ts.answer(state, args.claim)
    except TurnError as e:
        print(str(e), file=sys.stderr)
        rt.close()
        return 3            # 3 = 门禁拒绝，区别于 2 = 用法错误
    print(json.dumps(out, ensure_ascii=False, indent=2))
    rt.close()
    return 0


def cmd_turn_abstain(args) -> int:
    rt = _rt(args)
    ts = TurnSession(rt)
    try:
        state = ts.load(args.turn_id)
        out = ts.abstain(state, args.reason or "")
    except TurnError as e:
        print(str(e), file=sys.stderr)
        rt.close()
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    rt.close()
    return 0


def cmd_turn_auto(args) -> int:
    """规则驱动器：不做语义判断，只验证 packet 的信息足以驱动决策。"""
    rt = _rt(args)
    ts = TurnSession(rt)
    try:
        state = ts.start(
            args.question, seeds=args.concept, no_hop=args.no_hop,
            budget=BudgetLedger(tool_calls=args.max_calls, evidence_reads=args.max_reads),
            allow_qualified_answer=not args.strict_answer,
        )
        res = auto_run(ts, state, max_steps=args.max_steps, claim=args.claim)
    except (TurnError, ProviderError, ManifestError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        rt.close()
        return 2
    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"turn  : {res.turn_id}   {res.question}")
        for st in res.steps:
            tgt = f"  {st.view_node_id}" if st.view_node_id else ""
            obl = f"  [{st.obligation_id}]" if st.obligation_id else ""
            print(f"  {st.n:>2}. {st.action:<9}{obl}{tgt}   {st.note}")
        print(f"\nstop_reason: {res.stop_reason}")
        for oid, st in res.obligations.items():
            print(f"  {st:<10} {oid}")
    rt.close()
    return 0 if res.stop_reason in (
        "all_required_obligations_supported", "qualified_answer_with_gaps"
    ) else 3


def cmd_turn_show(args) -> int:
    rt = _rt(args)
    ts = TurnSession(rt)
    if not args.turn_id:
        ids = ts.list_ids()
        for tid in ids:
            st = ts.load(tid)
            print(f"{tid}  {st.status:10s}  {st.stop_reason or '-':38s}  {st.contract.question}")
        if not ids:
            print("（还没有轮次）")
        rt.close()
        return 0
    try:
        state = ts.load(args.turn_id)
        pk = ts.packet(state, node=args.node, limit=args.limit, cursor=args.cursor)
    except (TurnError, ManifestError) as e:
        print(str(e), file=sys.stderr)
        rt.close()
        return 2
    if args.json:
        print(json.dumps(state.to_dict() if args.full else pk, ensure_ascii=False, indent=2))
    else:
        _print_packet(pk)
    rt.close()
    return 0



def cmd_install_skills(args) -> int:
    try:
        results = install_skills(
            dest=Path(args.dest).expanduser() if args.dest else None,
            all_harnesses=args.all,
            dry_run=args.dry_run,
        )
    except InstallError as exc:
        print(f"install-skills 失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([
            {"harness": r.harness, "target": str(r.target),
             "installed": list(r.installed), "status": r.status, "detail": r.detail}
            for r in results
        ], ensure_ascii=False, indent=2))
        return 0 if any(r.status == "installed" for r in results) else 2

    failed = False
    for r in results:
        if r.status == "installed":
            note = f"  （{r.detail}）" if r.detail else ""
            print(f"  ✓ {r.harness:<12} {r.target}{note}")
            for name in r.installed:
                print(f"      {name}")
        elif r.status == "failed":
            failed = True
            print(f"  ✗ {r.harness:<12} {r.target}  {r.detail}", file=sys.stderr)
        else:
            print(f"  - {r.detail}", file=sys.stderr)
    if not any(r.status == "installed" for r in results):
        # 没铺成的 agent 需要能自己找到技能正文，否则只能卡在这里。
        print("没装上任何 harness。用 --all 强制铺到全部已知路径，或 --dest <目录> 指定。\n"
              f"不支持技能的 harness 直接读：{bundled_skills_dir()}",
              file=sys.stderr)
        return 2
    return 2 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="unistile", description="OKF Profile + 可热插拔证据后端 POC")
    ap.add_argument("--bundle", default="knowledge", help="OKF Bundle 根目录")
    ap.add_argument("--runtime", default="runtime", help="运行时派生物目录（可整体删除重建）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ad = sub.add_parser("add", help="纳入一份新文档：落 Resource → 生成 Concept → 校验 → 建索引")
    ad.add_argument("file")
    ad.add_argument("--uid", required=True, help="kn:<namespace>:<local_id>[:<qualifier>]")
    ad.add_argument("--title", required=True)
    ad.add_argument("--domain", required=True, help="domains/<domain>/")
    ad.add_argument("--type", default="Knowledge Concept")
    ad.add_argument("--description")
    ad.add_argument("--revision", type=int, default=1)
    ad.add_argument("--relation", action="append", help="<type>:<target_uid>，可重复")
    ad.add_argument("--tag", action="append")
    ad.add_argument("--external-id", dest="external_id")
    ad.add_argument("--alias", action="append",
                    help="别名，可重复。title 是 UUID 之类不可读的串时必须给，"
                         "否则这份文档在 resolve/门禁里寻址不到")
    ad.add_argument("--no-ingest", action="store_true")
    ad.set_defaults(fn=cmd_add)

    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("ingest").set_defaults(fn=cmd_ingest)
    sub.add_parser("providers").set_defaults(fn=cmd_providers)
    sub.add_parser("bindings").set_defaults(fn=cmd_bindings)

    rs = sub.add_parser("resolve", help="姓名/别名/external_id → uid；定位不到 exit 3")
    rs.add_argument("query")
    rs.add_argument("--limit", type=int, default=10)
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(fn=cmd_resolve)

    ins = sub.add_parser("install-skills", help="把 unistile 技能装到各 Agent Harness 的 skills 目录")
    ins.add_argument("--all", action="store_true", help="铺到全部已知路径，不管 harness 装没装")
    ins.add_argument("--dest", help="只装到这一个目录")
    ins.add_argument("--dry-run", action="store_true", help="只打印会写到哪里")
    ins.add_argument("--json", action="store_true")
    ins.set_defaults(fn=cmd_install_skills)

    tr = sub.add_parser("tree", help="逐层导航（多投影）")
    tr.add_argument("projection", nargs="?")
    tr.add_argument("--node", help="展开这个节点；不给则列根层")
    tr.add_argument("--limit", type=int, default=20)
    tr.add_argument("--offset", type=int, default=0)
    tr.set_defaults(fn=cmd_tree)

    wh = sub.add_parser("where", help="Concept 出现在哪些投影下")
    wh.add_argument("concept_uid")
    wh.set_defaults(fn=cmd_where)

    ol = sub.add_parser("outline", help="文档的 section 导航图（不检索）")
    ol.add_argument("concept_uid")
    ol.add_argument("--revision", type=int, default=None, help="默认取该 Concept 最新的 Binding revision")
    ol.add_argument("--preview", type=int, default=48, help="每节预览字符数")
    ol.set_defaults(fn=cmd_outline)

    a = sub.add_parser("ask")
    a.add_argument("query")
    a.add_argument("--concept", action="append", help="限定 Concept uid，可重复")
    a.add_argument("--obligation", action="append", help="Evidence Obligation id，可重复")
    a.add_argument("--limit", type=int, default=5)
    a.add_argument("--section", action="append", help="限制在该 section 路径下检索，可重复表示逐层路径")
    a.add_argument("--no-hop", action="store_true", help="不沿 amends/supersedes 展开范围")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_ask)

    tn = sub.add_parser("turn", help="一轮问答：义务派生 + 证据校验 + answer 门禁")
    tsub = tn.add_subparsers(dest="turn_cmd", required=True)

    ts_start = tsub.add_parser("start", help="开轮")
    ts_start.add_argument("question")
    ts_start.add_argument("--concept", action="append", help="限定 seed Concept uid，可重复")
    ts_start.add_argument("--no-hop", action="store_true", help="不沿 amends/supersedes 展开范围")
    ts_start.add_argument("--max-calls", type=int, default=8)
    ts_start.add_argument("--max-reads", type=int, default=5)
    ts_start.add_argument("--strict-answer", action="store_true",
                          help="不允许限制性结论：有 blocked 就必须 abstain")
    ts_start.add_argument("--json", action="store_true")
    ts_start.set_defaults(fn=cmd_turn_start)

    ts_act = tsub.add_parser("act", help="执行一次文档证据检索")
    ts_act.add_argument("turn_id")
    ts_act.add_argument("--obligation", required=True, help="要推进哪条义务")
    ts_act.add_argument("--view-node", dest="view_node",
                        help="从 manifest 挑一个入口精确读取（不检索，不必编查询词）")
    ts_act.add_argument("--query", help="关键词检索；默认用开轮时的问题")
    ts_act.add_argument("--limit", type=int, default=5)
    ts_act.add_argument("--section", action="append", help="限制在该 section 路径下，可重复")
    ts_act.add_argument("--json", action="store_true")
    ts_act.set_defaults(fn=cmd_turn_act)

    ts_ans = tsub.add_parser("answer", help="过门禁才输出；不过 exit 3")
    ts_ans.add_argument("turn_id")
    ts_ans.add_argument("--claim", required=True)
    ts_ans.set_defaults(fn=cmd_turn_answer)

    ts_abs = tsub.add_parser("abstain", help="明确拒答")
    ts_abs.add_argument("turn_id")
    ts_abs.add_argument("--reason")
    ts_abs.set_defaults(fn=cmd_turn_abstain)

    ts_auto = tsub.add_parser("auto", help="规则驱动器跑完一整轮（回归基线用，不做语义判断）")
    ts_auto.add_argument("question")
    ts_auto.add_argument("--concept", action="append")
    ts_auto.add_argument("--no-hop", action="store_true")
    ts_auto.add_argument("--claim", help="不给则只验证控制流")
    ts_auto.add_argument("--max-calls", type=int, default=8)
    ts_auto.add_argument("--max-reads", type=int, default=5)
    ts_auto.add_argument("--max-steps", type=int, default=20)
    ts_auto.add_argument("--strict-answer", action="store_true")
    ts_auto.add_argument("--json", action="store_true")
    ts_auto.set_defaults(fn=cmd_turn_auto)

    ts_show = tsub.add_parser("show", help="当前 packet；不给 id 则列出所有轮次")
    ts_show.add_argument("turn_id", nargs="?")
    ts_show.add_argument("--node", help="展开这个 view_node_id 的下一层；不给则列根层")
    ts_show.add_argument("--limit", type=int, default=8)
    ts_show.add_argument("--cursor", type=int, default=0)
    ts_show.add_argument("--full", action="store_true", help="含 trace 的完整状态")
    ts_show.add_argument("--json", action="store_true")
    ts_show.set_defaults(fn=cmd_turn_show)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
