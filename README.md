# unistile

**证据门禁运行时。** 从知识目录派生「回答前必须核实什么」，校验每条引用的来源、等级与可回读性，
义务没满足就不让答。

它不是检索系统，是拦截系统 —— 检索、投影、Provider 都是为门禁服务的配件。

对应设计：Obsidian `uniforce/OKF 格式规范落地方案：Profile、Binding 与可热插拔证据后端`
（以及 `Agent-native 多尺度知识体系设计：OKF + WeKnora`、`Context Graph：Agent 请求级上下文运行时设计`）。

## 与 OKF 的关系

unistile 是 [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
的一个变形：**收紧格式，再补上格式管不到的运行时。**

OKF 定义知识怎么存 —— 目录结构、Concept 文件、index.md 导航，`type` 是唯一始终必填的键。
它是一份格式规范，不涉及运行时。unistile 在此之上做两件事。

**一、收紧（unistile OKF Profile v1）**

| | OKF | unistile Profile v1 |
|---|---|---|
| 必填字段 | `type` | `type` `title` `status` `uid` `evidence_class` |
| uid | 无规定 | `kn:<ns>:<local>[:<qual>]`，namespace 须登记 |
| 关系类型 | 无规定 | 六种，须登记 |
| 关系元数据 | 无规定 | 可带，**且会被运行时当硬约束用** |
| 后端信息 | 无规定 | 13 个键禁止出现在 Concept |

**二、扩展（OKF 不涉及的运行时）**

Binding（后端热插拔）、归一化文本平面（跨 Provider 的 locator 坐标系）、
Turn 与 Obligation（证据门禁）。

**关键纪律：扩展全部落在 `runtime/`，一个字节都不进 Bundle。**
所以 unistile 的 Bundle 仍然是合法 OKF，别的 OKF 消费者能直接读。兼容是单向的：

```
unistile Bundle  →  合法 OKF     ✓
上游 OKF Bundle  →  unistile     ✗   缺 uid / status / evidence_class
```

名字取自 turnstile（闸机）。逻辑符号 `⊢` 也叫 turnstile，读作「可由…推出」——
这个系统做的就是判定 `证据 ⊢ 结论`，判不出来就不开闸。

## 两句话

1. **义务由 Runtime 从 Catalog 事实派生，外部 Agent 加得了、删不掉。**
   有 `amends` 入边就必须查补充文件；`answer` 不在 `legal_actions` 里，就是物理上答不了。
2. **Concept 只承载知识身份与逻辑资源指针**，"用哪个检索后端"是 Binding 的属性。
   POC 用 `local-fts` 跑通全链路，WeKnora 以能力声明 + 契约测试的形式保留，默认关闭。

## 安装

需要 Python 3.12+。

```bash
pip install git+https://github.com/Varybai/unistile.git
```

从源码装（要跑测试或改代码）：

```bash
git clone https://github.com/Varybai/unistile.git
cd unistile
uv venv --python 3.12 && uv pip install -e . && uv pip install pytest
```

装好之后 `unistile` 命令就能用了。没进 PATH 时用 `python -m unistile.cli <子命令>`，参数一样。

```bash
unistile --help
```

## 五分钟上手

仓库自带一个演示 Bundle（`knowledge/`），4 份文档 + 6 个 Concept。
里面埋了一个真实的坑：**原协议写质保 12 个月，补充协议改成了 24 个月。**

### 1. 建索引

```bash
unistile ingest
```

```
concepts=6  bound=5  index.md=3  projections=27 nodes in 3
  skipped kn:equipment:A-1007: 无 Provider 覆盖 evidence_class=structured
```

skipped 那行是正常的——A-1007 是结构化实体，没有可回读的原文。

### 2. 看看有什么

```bash
unistile tree                                    # 三个导航视图
unistile tree document-collection                # 展开其中一个
unistile outline kn:agreement:AGR0048            # 某份文档的章节图
```

### 3. 检索（无状态，没有门禁）

```bash
unistile ask "质保期限" --concept kn:agreement:AGR0048
```

会返回带 `char_span` 的原文片段，每条都能按 locator 精确回读。

### 4. 一轮带门禁的问答

这才是 unistile 的主线。开一轮：

```bash
unistile turn start "A-1007 的质保期是多久？" --concept kn:agreement:AGR0048
```

```
义务：
  ○ obl-original-source                    至少给出一段可回读的原文
  ○ obl-amendments:kn:agreement:AGR0048    确认（第 7.2 条）被 1 份补充文件修改的具体内容

gate  : allowed=False  required_obligation_unsupported
合法动作: ['inspect', 'abstain', 'expand', 'read', 'search_document_evidence', 'follow']
          （answer 不在列表里 —— 现在物理上答不了）
```

第二条义务是**算出来的**：AGR0048 有一条 `amends` 入边，所以补充协议必须查。
你删不掉它。

逐层导航到 7.2，读进来：

```bash
unistile turn show t-001 --node AGR0048        # 5 章
unistile turn show t-001 --node AGR0048#4      # 7.1 / 7.2 / 7.3
unistile turn act  t-001 --obligation obl-original-source --view-node AGR0048#6
```

现在试着回答——只看了原协议：

```bash
unistile turn answer t-001 --claim "12 个月"
```

```
门禁拒绝（required_obligation_unsupported）：
  obl-amendments:kn:agreement:AGR0048  unseen  —— 确认（第 7.2 条）被 1 份补充文件修改的具体内容
```

退出码 3。**12 个月是错的，门禁拦住了。** 补上补充协议：

```bash
unistile turn act t-001 --obligation obl-amendments:kn:agreement:AGR0048 --view-node AGR0048#6
unistile turn act t-001 --obligation obl-amendments:kn:agreement:AGR0048 --view-node Supplement-02#2
unistile turn answer t-001 --claim "24 个月（原协议 12 个月，已被补充协议二修改）"
```

退出码 0，输出带完整 Evidence Envelope：每条证据的 `concept_uid`、`section_path`、
`char_span`、`content_sha256`。

### 5. 让规则驱动器自己跑一遍

```bash
unistile turn auto "A-1007 的质保期是多久？" --concept kn:agreement:AGR0048
```

零语义判断，按义务优先级和字符成本挑路。它存在的意义是验证 packet 的信息够不够驱动决策，
以及给出可 diff 的参考轨迹。

### 6. 加一份自己的文档

```bash
unistile add 你的文件.pdf \
  --uid "kn:agreement:YOUR-DOC" \
  --title "文档标题" \
  --domain contracts \
  --description "一句话说明" \
  --relation "references:kn:equipment:A-1007"
```

支持 17 种后缀（docx/pdf/xlsx/pptx/odf/rtf/epub/csv/md/txt…）。
校验不通过会自动回滚，不会留下半截文件。

### 7. 跑测试

```bash
python -m pytest -q
```

## 作为 Skill 使用

`skills/` 下两个技能，任何支持 Agent Skills 的 harness 都能装：

| 技能 | 职责 | 触发 |
|---|---|---|
| `unistile-answer` | 在证据门禁下回答问题 | 从受控文档集回答，答错有代价 |
| `unistile-author` | 文档入库、写/修 Concept | 新文档、frontmatter 报错 |

克隆本仓库的话什么都不用做——`.claude/skills/` 和 `.cursor/skills/` 已经是指向
`skills/` 的符号链接，打开就能用。

装到别处：

```bash
cp -r skills/unistile-* ~/.claude/skills/     # 用户级，所有项目可用
```

不支持 skill 的 harness（如 Codex）：`AGENTS.md` 是 `unistile-answer` 的精简版，
或把 SKILL.md 去掉 frontmatter 拼进 system prompt。`reference/` 是按需加载的
补充材料，不要一股脑塞进去——它们存在就是为了让主文档保持短。

自检：让 agent 回答「A-1007 的质保期是多久？」。**它应该在第一次 answer 时被拦住**——
读到原协议的 12 个月就想回答，门禁以 `obl-amendments` 未满足拒绝（exit 3），
去读补充协议后才答出 24 个月。直接答 12 个月说明技能没生效。

## 命令

| 命令 | 作用 |
|---|---|
| `unistile add <文件>` | 纳入新文档：落 Resource → 生成 Concept（自动算 sha256）→ 校验 → 建索引 |
| `unistile validate` | L0/L1/L2 + 值级往返校验；未通过不允许进 Catalog |
| `unistile ingest` | 校验 → 归一化文本平面 → Catalog → Provider bind → 重建 index.md 与投影 |
| `unistile tree [投影] [--node ID]` | 逐层导航，含 omission 统计 |
| `unistile where <uid>` | 这个 Concept 出现在哪些投影下 |
| `unistile providers` | 列出 Provider 及其 capabilities 声明（含已关闭的 weknora） |
| `unistile bindings` | 列出 Binding 的 role / status / stale 状态 |
| `unistile ask` | 受控范围检索 + 回读校验 + Evidence Envelope（无状态） |
| `unistile turn start/act/answer/abstain` | 一轮问答：义务派生 → 证据校验 → answer 门禁（有状态） |
| `unistile turn show [--node ID]` | Local Navigation Manifest：逐层展开、覆盖提示、省略与 cursor |
| `unistile turn auto` | 规则驱动器跑完整轮（回归基线用，不做语义判断） |

`ask` 默认沿已登记的 `amends`/`supersedes` 关系有界展开；`--no-hop` 关闭。
对比这两种输出可以看到：不展开时只能看到原协议的 12 个月，展开后才看到补充协议改成 24 个月。

### turn：Runtime 当裁判，不当司机

驾驶循环在外部 Agent 手里，Runtime 只在每次调用时更新义务状态并守门。
义务由 Runtime 从 Catalog 事实派生（`amends` 入边 → 必须查补充协议），
外部 Agent 加得了、删不掉 —— 否则它可以写出 `obligations=[]` 让门禁自动通过。

```
unistile turn start "A-1007 的质保期是多久？" --concept kn:agreement:AGR0048
unistile turn act   t-001 --obligation obl-original-source --query 质保期限
unistile turn answer t-001 --claim "12 个月"        # exit 3：obl-amendments 还没查
unistile turn act   t-001 --obligation obl-amendments:kn:agreement:AGR0048 --query 质保期限
unistile turn answer t-001 --claim "24 个月"        # exit 0
```

### Local Navigation Manifest：不用编查询词

`turn show --node` 逐层展开，`turn act --view-node` 精确读取。全程没有自造的关键词：

```
unistile turn show t-001                      # 根层：scope 里还有未满足义务的 Concept
unistile turn show t-001 --node AGR0048       # 5 章
unistile turn show t-001 --node AGR0048#4     # 7.1 / 7.2 / 7.3
unistile turn act  t-001 --obligation obl-original-source --view-node AGR0048#6
```

每个入口带 `coverage_hints`（落在哪条义务的范围内）、字符成本和预览。
**hint 的边界是诚实的**：Concept 级是集合运算（`concept_uid ∈ obligation.scope_uids`，确定）；
Section 级不声明"命中"，只声明"在范围内"。收益不是提示更准，
是候选集从无限（任意查询词）收敛成有限（几个章节）。

`--view-node` 走 `read` 动作：直接从归一化文本平面按字符区间取原文，
不经过任何 Provider，证据等级 `original-resource`，`provider_id=runtime:read`、`score_kind=none`
（Agent 指定的区间，没有排序分数可言）。

### turn auto：验证 packet 的信息够用

```
unistile turn auto "A-1007 的质保期是多久？" --concept kn:agreement:AGR0048
```

`RuleSelector` 零语义判断 —— 挑义务按优先级，挑入口按字符成本。
它存在的理由是**验证 packet 的信息足以驱动决策**：`coverage_hints`、成本、省略、`legal_actions`
在此之前没有任何东西真的消费过。最笨的消费者能走通，说明信息够用；
走不通就是 Runtime 少给了东西。同时它给出可 diff 的参考轨迹（`tests/golden/turn_set.json`，5 条）：
任何驾驶者跑同一个问题，`stop_reason` 与它不同，只有两种可能 —— 找到了更短的路，或绕过了门禁。

它立刻暴露了一个单步测试看不见的缺口：一条便宜的原文就能满足「确认修改了什么」，
读到的两段都不含答案，门禁照样放行。**门禁校验来源与等级，不校验语义。**

### 聚合门槛：让登记的治理事实有牙齿

`amends` 边上带着 `metadata: {clause: "7.2"}`。收紧前它只是装饰品 —— 存着、显示着，
没有任何东西读它做决定。现在它变成硬约束：

| 门槛 | 来源 | 作用 |
|---|---|---|
| `required_section` | 边元数据 `clause` → 目标文档的对应 section | 被点名的原条款必须在场 |
| `min_distinct_concepts=2` | amends 是二元关系 | 原协议和补充协议两端都要读 |
| `min_evidence_count=2` | 同上 | 一段不够 |

全是集合运算，零语义判断。编号体系对不上（补充协议写「第七条第二款」、原协议标题写「7.2」）时
降级为「至少读到目标文档」，并在义务上记 `hint_degraded` —— 匹配是精度手段，
不该把整份文档变成不可回答。

门槛够不着时开轮即 `blocked`，不烧预算：`--no-hop` 让补充协议不在范围里，
两来源门槛无解，第一步就给限制性结论并公开缺口。

剩下的半个缺口诚实记在 `turn_set.json` 的 `semantic_gap` 里，每次跑测试都打印：
原条款 12 个月已被强制读入，新值 24 个月仍可能不在场。
**门禁保证有据可查、关键条款在场，不保证证据支持结论。**

Manifest 同时公开**看不见的部分** —— `child_count` / `returned` / `omission_summary` / `next_cursor`，
义务已满足的 Concept 折叠进省略说明。Agent 不会把"没展示"当成"不存在"。

这一层要证明的不是"能查到证据"，而是**查不全就答不了**：
只读原协议会答出 12 个月（错的），门禁拦在补充协议之前。
`answer` 不在 `legal_actions` 里时，Agent 物理上答不了。

轮次状态落在 `runtime/turns/<turn_id>.json`，是派生物，删掉不影响 `knowledge/`。

## 文档解析：anydoc

非 Markdown 文档一律经 [firecrawl/anydoc](https://github.com/firecrawl/anydoc)
转成 GFM Markdown，再进归一化文本平面：

```
.docx/.pptx/.xlsx/.odt/.rtf/.epub/.csv/.pdf
  → anydoc.to_markdown()      纯 Rust，无 ML 模型，无外部服务，无 API key
  → _markdown_outline()       标题层级、char offset
  → ResourceStore             text.txt + meta.json，按内容哈希键控
```

平面本来就以 Markdown 为坐标系，所以 outline、offset、回读、locator 一行都不用改。
`.md` / `.txt` 本身就是纯文本，不经 anydoc（送进去反而会把 `#` 转义成 `\#`）。

**换抽取器就是换坐标系**，因此版本号进 `extractor_version`：

```
text/markdown  →  normalizer-v1
application/pdf →  anydoc-0.2.3+normalizer-v1
```

老 locator 靠内容哈希键控的目录继续可读，新旧并存。

**anydoc 给不了页边界。** 于是它抽出的资源 `pages=[]`、`locator.page` 缺省 ——
对 PDF 谎称 page=1 比不给更糟。`.md`/`.txt` 是单一无分页文本流，
"第 1 页"是精确描述而非猜测，照旧保留。

抽取失败按原因分类（加密 / 缺部件 / 结构损坏 / 超安全上限 / 需要 OCR），
抛 `ExtractionFailed`；`ingest` 跳过这一份并记下原因，**不让整个 bundle 失败**，
对应 Obligation 判 blocked。扫描版 PDF 需要 OCR，anydoc 不做 —— 这条会明确说出来。

演示 bundle 里 `A-1007-维护服务协议.docx` 就是走这条路进来的：

```
unistile outline kn:equipment:A-1007:service-agreement
→ extractor: anydoc-0.2.3+normalizer-v1   pages: 0
→ 4 sections，char offset 精确到 [104:154]
```

## 代码地图

| 路径 | 对应方案章节 | 职责 |
|---|---|---|
| `unistile/spec/` | §2 Profile | 字段分层、uid 语法、Schema Registry、validator |
| `unistile/resources/anydoc_extract.py` | §4 | anydoc 抽取层：docx/pdf/… → GFM；错误分类与版本号 |
| `unistile/resources/normalizer.py` | §4 | 归一化文本平面：跨 Provider 的 locator 基准 |
| `unistile/catalog/` | §3.2 | concepts / concept_edges / resource_revisions / resource_bindings |
| `unistile/evidence/contract.py` | §5 | Provider 契约与能力声明 |
| `unistile/evidence/registry.py` `routing.py` | §3.1 | 注册与按维度路由（局部热插拔） |
| `unistile/evidence/providers/` | §6、§7.2 | local-fts / null / weknora(v0) |
| `unistile/evidence/adapter.py` | §5、§13.2 | 跨系统校验：scope / stale / 回读 / 证据等级 |
| `unistile/envelope.py` | §13 | Evidence Envelope |
| `unistile/indexgen.py` | 上游 SPEC §8 | index.md 自动生成（派生物，勿手改） |
| `unistile/projections.py` | §6 多视图投影 | query_backed（document-collection / lifecycle 规则派生）+ materialized（business.yaml） |
| `unistile/turn/obligations.py` | Context Graph §11.1 | 义务派生：L1 结构派生（Catalog 事实）+ L3 兜底 |
| `unistile/turn/ledger.py` | Context Graph §11.1 | unseen/candidate/supported/blocked 状态机 + answer 门禁 |
| `unistile/turn/manifest.py` | Context Graph §11.2 | Local Navigation Manifest：coverage_hints、成本、省略与 cursor |
| `unistile/turn/session.py` | Context Graph §7、§15 | 轮次状态存取、合法动作执行、Trace |
| `unistile/turn/driver.py` | Context Graph §11.5 | RuleSelector：packet → 动作；整轮回归基线 |
| `tests/conformance/` `tests/golden/` | §7.3 | C1–C9 契约测试与对照基准 |

## 已实现 / 未实现

**已实现**：Profile 校验门禁、多投影导航树（同一 Concept 出现在业务/资料/时效三个视图；后两个由规则派生，新文档自动收录）、关系边元数据（amends 记录被改的条款）、index.md 按上游 SPEC §8 自动生成（条目带 description）、uid 语法与 namespace 登记、归一化文本平面与精确回读、
Catalog 与多 Provider Binding（role: primary/shadow/retired）、Provider 契约与能力声明、
多格式文档解析（anydoc：docx/pptx/xlsx/odf/rtf/epub/csv/pdf → GFM → 同一文本平面）、
local-fts（FTS5 trigram，中文可用）、null provider、weknora 能力声明、
跨系统校验（scope 越界 / binding stale / locator 哈希漂移 / 能力诚实）、
有界关系展开、Evidence Envelope、C1–C9 测试、
Turn Runtime（义务从 Catalog 事实派生、四状态机、多维预算与 verify/answer 预留、
answer/qualified/abstain 三出口门禁、轮次状态持久化与 Trace）、
Local Navigation Manifest（逐层展开、coverage_hints、字符成本、省略公开与分页、
read 动作按字符区间精确取原文）、
整轮控制流基线（RuleSelector + 5 条 golden 轨迹，断言 stop_reason / 读取轨迹 / 义务终态）、
聚合门槛（边元数据 clause → 被点名条款必须在场；amends 两端都要读；够不着时开轮即 blocked）。

**未实现（与方案一致，能力缺失均由 capabilities() 显式声明）**：
页边界（anydoc 不保留，locator.page 对非 md/txt 缺省）、扫描版 PDF（需 OCR，anydoc 不做）、
向量与混合检索、rerank/MMR、图片理解、
WeKnora 实际接入（SSE 映射、session 生命周期、scope_binding_ids 编译、偏移对齐）、
义务的语义门槛（来源、条数、来源数、被点名条款都能查，"这段话是否回答了问题"查不了 —— 见 `semantic_gap`）、
L2 任务形状义务（compare/汇总类问题，等 L3 兜底命中率上来再接）、
跨轮 Session State 与指代消解、Value Vector 与有界 Hopping、dependency-directed freshness。

## 证据边界

本 POC 验证的是**契约与控制流**，不是检索质量。golden set 只有 8 条、语料只有 3 篇文档，
`core recall@3 = 6/6` 不能外推到真实知识库。known_limitation 的 2 条记录了 keyword-only 的能力边界，
接入向量 Provider 后应当翻转。
