# packet 字段

`unistile turn start/act/show --json` 返回同一个结构。

```json
{
  "turn_id": "t-001",
  "status": "open | answered | abstained",
  "question": "用户原话",
  "scope_uids": ["本轮允许触及的 Concept，沿 amends/supersedes 有界展开而来"],
  "obligations": [ ... ],
  "gate": {"allowed": false, "stop_reason": "...", "unsupported": [], "blocked": []},
  "legal_actions": ["inspect", "abstain", "expand", "read", "search_document_evidence", "follow"],
  "manifest": { ... },
  "budget": {"tool_calls": "1/8", "evidence_reads": "1/5",
             "tokens_available": 9197, "reserved": 2700}
}
```

## obligations[]

| 字段 | 含义 |
|---|---|
| `id` | `obl-original-source` 是兜底；`obl-amendments:<uid>` 由 `amends` 入边派生 |
| `status` | `unseen` → `candidate` → `supported`，或 `blocked` |
| `requirement` | 人话描述这条要查什么 |
| `scope_uids` | **只有这些 Concept 的证据算数**。拿甲文件的原文满足不了乙文件的义务 |
| `minimum_evidence_level` | `original-resource` 要求能按 char_span 精确回读 |
| `min_evidence_count` / `min_distinct_concepts` | 聚合门槛。amends 是二元关系，两端都要读 |
| `required_section` | 来自关系边元数据的 `clause`，例如「7.2 质保期限」必须在场 |
| `hint_degraded` | 条款编号在目标文档里对不上时的降级说明 |
| `blocked_reason` | 为什么走死了。abstain 时转述给用户 |
| `derived_from` | 派生依据，审计用 |

`blocked` 的常见原因：

```
scope 为空                    范围内没有可回读原文的 Concept（如 structured 数据）
本轮范围内只够得着 N 个来源    门槛要求更多，--no-hop 时常见
capability.insufficient       Provider 天花板低于任务要求，查一万次也没用
候选全部被拒                  locator 哈希漂移、索引落后于资源
```

## gate.stop_reason

| 值 | 含义 |
|---|---|
| `all_required_obligations_supported` | 可以答 |
| `qualified_answer_with_gaps` | 可以答，但必须公开 `unresolved_gaps` |
| `required_obligation_unsupported` | 还没查够 |
| `abstain_blocked_obligation` | 关键义务走死，只能拒答 |
| `budget_exhausted` | 预算耗尽，返回已确认事实和缺口，不编 |

## manifest

```json
{
  "node": null,
  "child_handles": [
    {"view_node_id": "AGR0048#6", "head": "7.2 质保期限", "kind": "section",
     "concept_uid": "kn:agreement:AGR0048",
     "coverage_hints": ["obl-original-source"],
     "hint_basis": "concept-in-obligation-scope",
     "section_path": ["...", "7. 质量保证", "7.2 质保期限"],
     "char_span": [310, 390], "preview": "设备质保期为自最终验收合格之日起 12 个月…",
     "child_count": 0, "expected_cost": {"tokens": 40, "reads": 1}}
  ],
  "child_count": 5, "returned": 2,
  "omission_summary": "另有 3 项未列出（3. 价格与付款 / 7. 质量保证 / 9. 争议解决）",
  "next_cursor": 2
}
```

- `node: null` 是根层：scope 里**还有未满足义务**的 Concept。义务满足的会折叠进 `omission_summary`。
- `child_count > 0` 的 handle 还能再展开一层：`unistile turn show <id> --node <view_node_id>`。
- `child_count == 0` 且 `kind == "section"` 才能 `--view-node` 直接读。
- `hint_basis` 恒为 `concept-in-obligation-scope` —— 这是集合运算，不是相关性判断。

## 预算

`tokens_available` 已经扣掉 `reserved`（verify + answer 预留）。
检索花不到那部分，所以门禁开的时候一定还有 token 可以输出。
