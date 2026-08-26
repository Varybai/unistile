---
name: unistile-answer
description: Answer questions from a unistile knowledge bundle — OKF with a tightened profile — under an evidence gate. The runtime derives what must be verified from the catalog, checks every citation's source, level and readability, and refuses to let you answer until required obligations are supported. Use when answering from a governed document set where a wrong or unsourced answer matters (contracts, specs, equipment records), or when the user mentions unistile, OKF, a knowledge bundle, or evidence obligations.
license: MIT
metadata:
  tags: "RAG, knowledge, evidence, citations, governance"
  category: "knowledge"
---

# unistile-answer

你是这一轮问答的**驾驶者**。Runtime 是裁判：它派生义务、校验证据、守 answer 门禁。
选哪一步是你的事；能不能答是它的事。

## 先确认环境

```bash
unistile --help >/dev/null 2>&1 && echo OK || echo "未安装"
```

未安装：`pip install git+https://github.com/Varybai/unistile.git`
（本技能目录不是项目目录，别在这里找源码。）

还需要一个 OKF Bundle 目录，默认当前目录下的 `knowledge/`，用 `--bundle <路径>` 指定。
首次或文档有变动时先建索引：

```bash
unistile ingest
```

**所有 turn 命令都加 `--json`。** 不带 `--json` 的输出是给人看的，字段不稳定。

## 一轮的流程

```bash
unistile turn start "<用户的问题>" --concept <uid> --json
unistile turn show <turn_id> --node <view_node_id> --json
unistile turn act  <turn_id> --obligation <id> --view-node <view_node_id> --json
unistile turn answer <turn_id> --claim "<你的结论>"
unistile turn abstain <turn_id> --reason "<为什么答不了>"
```

不知道 `--concept` 填什么就先找：

```bash
unistile tree                      # 逐层导航
unistile where <uid>               # 这个 Concept 在哪些视图下
```

`--concept` 省略时 Runtime 会按标题/别名解析；解析不到会直接报错，
**不会退化成全库无界检索**。

## 五条规则

1. **`answer` 不在 `legal_actions` 里就别调。** 会被拒（exit 3），白花一次。
   先看 `gate.stop_reason` 和还没 `supported` 的义务。
2. **义务删不掉也改不了。** 它由 Runtime 从 Catalog 事实派生——有 `amends` 入边就必须
   查补充文件。你只能通过读到合格证据把它推到 `supported`。
3. **`omission_summary` 不是废话。** 「没展示」不等于「不存在」。要看更多用 `--cursor`。
4. **`coverage_hints` 只说"在这条义务的范围内"，不说"命中"。**
   哪一节真的写了答案，看 `head` 和 `preview` 自己判断——这正是需要你的地方。
5. **`--view-node` 优于 `--query`。** 前者精确读一段，后者是关键词检索，猜错词白花预算。

## 选下一步

```
gate.allowed == true              → answer，claim 必须能被已读证据支撑
有 required 义务未 supported      → 挑它，从 manifest 找覆盖它的入口
manifest 只有 concept 节点        → 先 show --node <它> 展开一层
所有 required 义务都 blocked      → abstain，把 blocked_reason 转述给用户
budget 快耗尽                     → answer（若门禁开）或 abstain，别硬撑
```

## 退出码

| 码 | 含义 | 怎么办 |
|---|---|---|
| 0 | 成功 | 继续 |
| 2 | 用法错误（未知 turn_id / 义务 / view_node，或预算不足） | 读 stderr 改参数 |
| 3 | 门禁拒绝或 abstain | **不要重试 answer**，先补证据 |

## 回答的时候

`answer` 返回的 `evidence[]` 每条带 `concept_uid`、`section_path`、`char_span`、
`content_sha256`。引用时用 `section_path`，别只说"根据文档"。

`stop_reason == "qualified_answer_with_gaps"` 时，`unresolved_gaps` 里的义务没满足——
**必须在回答里明说哪部分没核实**，不能当作已确认。

## 一轮完整的样子

问「A-1007 的质保期是多久？」——原协议写 12 个月，补充协议改成 24 个月：

```bash
unistile turn start "A-1007 的质保期是多久？" --concept kn:agreement:AGR0048 --json
#   两条义务：obl-original-source、obl-amendments:kn:agreement:AGR0048
#   gate.allowed=false，legal_actions 里没有 answer

unistile turn show t-001 --node AGR0048 --json        # 5 章
unistile turn show t-001 --node AGR0048#4 --json      # 7.1 / 7.2 / 7.3
unistile turn act t-001 --obligation obl-original-source --view-node AGR0048#6 --json

unistile turn answer t-001 --claim "12 个月"
#   exit 3 —— obl-amendments 还没查。12 个月是错的，门禁拦住了

unistile turn act t-001 --obligation obl-amendments:kn:agreement:AGR0048 \
  --view-node AGR0048#6 --json                        # 被点名的原条款 7.2
unistile turn act t-001 --obligation obl-amendments:kn:agreement:AGR0048 \
  --view-node Supplement-02#2 --json                  # 修改后的内容
unistile turn answer t-001 --claim "24 个月（原协议 12 个月，已被补充协议二修改）"
#   exit 0，stop_reason=all_required_obligations_supported
```

不带 `--node` 的 `unistile turn show <turn_id> --json` 随时拿当前状态。

## 字段不确定时

读 `reference/packet.md` —— packet 每个字段、每个 `stop_reason`、
每种 `blocked_reason` 的含义都在里面。不要靠猜。
