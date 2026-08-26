# 驱动 unistile turn

你是这一轮问答的驾驶者。Runtime 是裁判，不是司机：它派生义务、校验证据、守 answer 门禁；
选哪一步是你的事。

## 一轮的流程

```bash
unistile turn start "<用户的问题>" --concept <uid> --json     # 开轮，拿到第一个 packet
unistile turn show <turn_id> --node <view_node_id> --json     # 展开一层导航
unistile turn act <turn_id> --obligation <id> --view-node <id> --json   # 读一段原文
unistile turn answer <turn_id> --claim "<你的结论>"           # 过门禁才输出
unistile turn abstain <turn_id> --reason "<为什么答不了>"
```

`unistile turn show <turn_id> --json`（不带 `--node`）随时拿当前状态。

## 读 packet

```json
{
  "obligations": [
    {"id": "...", "status": "unseen|candidate|supported|blocked",
     "requirement": "人话描述这条要查什么",
     "scope_uids": ["只有这些 Concept 的证据算数"],
     "min_evidence_count": 2, "min_distinct_concepts": 2,
     "required_section": "7.2 质保期限"}
  ],
  "gate": {"allowed": false, "stop_reason": "required_obligation_unsupported"},
  "legal_actions": ["inspect", "abstain", "expand", "read", "search_document_evidence", "follow"],
  "manifest": {
    "child_handles": [
      {"view_node_id": "AGR0048#6", "head": "7.2 质保期限",
       "coverage_hints": ["obl-original-source"], "hint_basis": "concept-in-obligation-scope",
       "child_count": 0, "expected_cost": {"tokens": 40, "reads": 1}}
    ],
    "child_count": 5, "returned": 2,
    "omission_summary": "另有 3 项未列出（3. 价格与付款 / 7. 质量保证 / 9. 争议解决）",
    "next_cursor": 2
  },
  "budget": {"tool_calls": "1/8", "evidence_reads": "1/5", "tokens_available": 9197}
}
```

## 规则

1. **`answer` 不在 `legal_actions` 里就别调它**——会被拒，白花一次。先看 `gate.stop_reason`
   和还没 `supported` 的义务。
2. **义务删不掉也改不了。** 你只能通过读到合格证据把它推到 `supported`。
3. **`omission_summary` 不是废话。** 「没展示」不等于「不存在」。要看更多就用 `--cursor`。
4. **`coverage_hints` 只说"在这条义务的范围内"，不说"命中"。** 哪一节真的写了答案，
   看 `head` 和 `preview` 自己判断——这正是需要你的地方。
5. **`--view-node` 优于 `--query`。** 前者精确读一段，后者是关键词检索，猜错词就白花预算。

## 选下一步

```
gate.allowed == true                → answer，claim 要能被已读证据支撑
有 required 义务未 supported        → 挑它，从 manifest 里找覆盖它的入口
manifest 只有 concept 节点          → 先 show --node <它> 展开一层
所有 required 义务都 blocked        → abstain，把 blocked_reason 转述给用户
budget 快耗尽                       → answer（若门禁开）或 abstain，别硬撑
```

## 退出码

| 码 | 含义 | 怎么办 |
|---|---|---|
| 0 | 成功 | 继续 |
| 2 | 用法错误（未知 turn_id / 义务 / view_node，或预算不足） | 读 stderr，改参数重试 |
| 3 | 门禁拒绝或 abstain | **不要重试 answer**，先补证据 |

## 回答的时候

`answer` 返回的 `evidence[]` 每条带 `concept_uid`、`section_path`、`char_span`、
`content_sha256`。引用时用 `section_path`，别只说"根据文档"。

`stop_reason == "qualified_answer_with_gaps"` 时，`unresolved_gaps` 里的义务没满足——
必须在回答里明说哪部分没核实，不能当作已确认。
