# unistile

证据门禁运行时。你从知识 Bundle 回答问题时，它派生「回答前必须核实什么」，
校验每条引用，**义务没满足就物理上不让你回答**。

Runtime 是裁判不是司机：它出义务、验证据、守门禁；选哪一步是你的事。

## 先装好

```bash
unistile --help >/dev/null 2>&1 || pip install git+https://github.com/Varybai/unistile.git
unistile install-skills          # 把技能铺到本机各 harness 的 skills 目录
```

## 一轮的流程

```bash
unistile ingest                                                        # 建索引（Bundle 变了才要）
unistile turn start "<用户的问题>" --concept <uid> --json               # 开轮，拿 packet
unistile turn show  <turn_id> --node <view_node_id> --json             # 展开一层导航
unistile turn act   <turn_id> --obligation <id> --view-node <id> --json # 读一段原文
unistile turn answer  <turn_id> --claim "<你的结论>"                    # 过门禁才输出
unistile turn abstain <turn_id> --reason "<为什么答不了>"
```

`unistile turn show <turn_id> --json`（不带 `--node`）随时拿当前状态。

## packet 里看什么

| 字段 | 你要拿它做什么 |
|---|---|
| `obligations[].status` | `unseen`/`candidate` 的就是还欠的债；`blocked` 的补不上 |
| `obligations[].scope_uids` | 只有这些 Concept 的证据算数，读别处白读 |
| `obligations[].required_section` | 这一节的原文必须读到，缺了不给过 |
| `gate.stop_reason` | 门禁为什么不开 |
| `legal_actions` | **现在能调什么**。`answer` 不在里面就是答不了 |
| `manifest.child_handles[]` | 下一步能去哪：`head`、`preview`、`coverage_hints`、`expected_cost` |
| `manifest.omission_summary` | 没列全，用 `--cursor` 翻页 |
| `budget` | 还剩几次调用、几次读、多少 token |

## 五条硬规则

1. **`answer` 不在 `legal_actions` 里就别调**——必被拒（exit 3），白花一次预算。
   先看 `gate.stop_reason` 和还没 `supported` 的义务。
2. **义务删不掉也改不了。** 唯一的推进方式是读到合格证据。
3. **`coverage_hints` 只说「在这条义务的范围内」，不说「命中」。** 哪一节真写了答案，
   看 `head` 和 `preview` 自己判断——这正是需要你的地方。
4. **`--view-node` 优于 `--query`。** 前者精确读一段，后者猜关键词，猜错就白花预算。
5. **`omission_summary` 不是废话。** 「没展示」不等于「不存在」。

## 选下一步

```
gate.allowed == true               → answer，claim 必须被已读证据支撑
有 required 义务未 supported       → 挑它，从 manifest 找覆盖它的入口
manifest 只有 concept 节点         → 先 show --node <它> 展开一层
所有 required 义务都 blocked       → abstain，把 blocked_reason 转述给用户
预算快耗尽                          → answer（若门禁开）或 abstain，别硬撑
```

## 退出码

| 码 | 含义 | 怎么办 |
|---|---|---|
| 0 | 成功 | 继续 |
| 2 | 用法错误（未知 turn_id / 义务 / view_node，或预算不足） | 读 stderr，改参数重试 |
| 3 | 门禁拒绝或 abstain | **不要重试 answer**，先补证据 |

## 回答的时候

`answer` 返回的 `evidence[]` 每条带 `concept_uid`、`section_path`、`char_span`、
`content_sha256`。引用时用 `section_path`，别只说「根据文档」。

`stop_reason == "qualified_answer_with_gaps"` 时，`unresolved_gaps` 里的义务没满足——
**必须在回答里明说哪部分没核实**，不能当作已确认。

## 往 Bundle 里加文档

```bash
unistile add <文件> --uid "kn:<namespace>:<local_id>" --title "<标题>" \
  --domain <domain> --description "<一句话>" --relation "<type>:<target_uid>"
```

支持 17 种后缀（docx/pdf/xlsx/pptx/odf/rtf/epub/csv/md/txt…）。校验不过会自动回滚。
frontmatter 报错、uid 语法、六种关系类型：见 `unistile-author` 技能。

## 自检

问「A-1007 的质保期是多久？」。**第一次 answer 应该被拦住**——读到原协议写 12 个月
就想答，门禁以 `obl-amendments` 未满足拒绝（exit 3）；读完补充协议才答出 24 个月。
直接答 12 个月说明这份说明没生效。
