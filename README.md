# unistile

**证据门禁运行时。** 从知识目录派生「回答前必须核实什么」，校验每条引用的来源、等级与可回读性，
义务没满足就不让答。

它不是检索系统，是拦截系统 —— 检索、投影、Provider 都是为门禁服务的配件。

名字取自 turnstile（闸机）。逻辑符号 `⊢` 也叫 turnstile，读作「可由…推出」——
这个系统做的就是判定 `证据 ⊢ 结论`，判不出来就不开闸。

## 给 Agent 的一段话

把下面这段贴给 Codex / Claude Code / Pi / OMP / Grok，它就会自己装好：

> 运行 `pip install git+https://github.com/Varybai/unistile.git && unistile install-skills`，
> 再用 `unistile --help` 确认可用。unistile 是证据门禁运行时：从受控文档集回答问题时，
> 它派生「回答前必须核实什么」，校验每条引用，义务没满足就不让你回答。
> 装完之后从知识 Bundle 回答问题一律走 `unistile turn`，用法见 `unistile-answer` 技能。
> 自检：问「A-1007 的质保期是多久？」，第一次 answer 应该被拦（exit 3），
> 补上补充协议后答 24 个月才对。

用法细节不用抄进 prompt —— `install-skills` 铺完，harness 自己会加载技能。
装不上技能的 harness 读 [`AGENTS.md`](AGENTS.md)（完整说明，多数 harness 自动读取）；
[`CLAUDE.md`](CLAUDE.md) 是 Claude Code 的适配，用 `@AGENTS.md` 导入同一份正文。

## 安装

需要 Python 3.12+。

```bash
pip install git+https://github.com/Varybai/unistile.git
unistile install-skills
```

`install-skills` 只往**已经装了的** harness 写（marker 目录存在才动），当前覆盖：

| Harness | 目标目录 |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |
| Cursor | `~/.cursor/skills/` |
| Grok | `~/.grok/skills/` |
| Pi / OMP | `~/.pi/agent/skills/`、`~/.omp/agent/skills/` |

`--dry-run` 先看会写到哪；`--all` 不管装没装全铺一遍；`--dest <目录>` 指定单个目标。

从源码装（要跑测试或改代码）：

```bash
git clone https://github.com/Varybai/unistile.git
cd unistile && pip install -e ".[dev]" && python -m pytest -q
```

克隆下来 `.claude/skills/` 和 `.cursor/skills/` 已经是指向 `skills/` 的符号链接，打开就能用。

## 五分钟上手

仓库自带演示 Bundle（`knowledge/`），4 份文档 + 6 个 Concept，
里面埋了一个真实的坑：**原协议写质保 12 个月，补充协议改成了 24 个月。**

```bash
unistile ingest        # concepts=6  bound=5  index.md=3  projections=27 nodes in 3
unistile tree          # 三个导航视图
```

开一轮：

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

第二条义务是**算出来的**：AGR0048 有一条 `amends` 入边，所以补充协议必须查。你删不掉它。

逐层导航到 7.2 读进来，然后只凭原协议作答：

```bash
unistile turn show t-001 --node AGR0048        # 5 章
unistile turn show t-001 --node AGR0048#4      # 7.1 / 7.2 / 7.3
unistile turn act  t-001 --obligation obl-original-source --view-node AGR0048#6
unistile turn answer t-001 --claim "12 个月"
```

```
门禁拒绝（required_obligation_unsupported）：
  obl-amendments:kn:agreement:AGR0048  unseen  —— 确认（第 7.2 条）被 1 份补充文件修改的具体内容
```

退出码 3。**12 个月是错的，门禁拦住了。** 补上补充协议才放行：

```bash
unistile turn act t-001 --obligation obl-amendments:kn:agreement:AGR0048 --view-node AGR0048#6
unistile turn act t-001 --obligation obl-amendments:kn:agreement:AGR0048 --view-node Supplement-02#2
unistile turn answer t-001 --claim "24 个月（原协议 12 个月，已被补充协议二修改）"
```

退出码 0，输出带完整 Evidence Envelope：每条证据的 `concept_uid`、`section_path`、
`char_span`、`content_sha256`。

加一份自己的文档（支持 17 种后缀，校验不过自动回滚）：

```bash
unistile add 你的文件.pdf --uid "kn:agreement:YOUR-DOC" --title "文档标题" \
  --domain contracts --description "一句话说明" --relation "references:kn:equipment:A-1007"
```

## 与 OKF 的关系

unistile 是 [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
的一个变形：**收紧格式，再补上格式管不到的运行时。**

OKF 定义知识怎么存 —— 目录结构、Concept 文件、index.md 导航，`type` 是唯一始终必填的键。
它是格式规范，不涉及运行时。unistile 在此之上做两件事。

**一、收紧（unistile OKF Profile v1）**

| | OKF | unistile Profile v1 |
|---|---|---|
| 必填字段 | `type` | `type` `title` `status` `uid` `evidence_class` |
| uid | 无规定 | `kn:<ns>:<local>[:<qual>]`，namespace 须登记 |
| 关系类型 | 无规定 | 六种，须登记 |
| 关系元数据 | 无规定 | 可带，**且会被运行时当硬约束用** |
| 后端信息 | 无规定 | 13 个键禁止出现在 Concept |

**二、扩展**：Binding（后端热插拔）、归一化文本平面（跨 Provider 的 locator 坐标系）、
Turn 与 Obligation（证据门禁）。

**关键纪律：扩展全部落在 `runtime/`，一个字节都不进 Bundle。** 所以兼容是单向的：

```
unistile Bundle  →  合法 OKF     ✓
上游 OKF Bundle  →  unistile     ✗   缺 uid / status / evidence_class
```

两条设计底线：

1. **义务由 Runtime 从 Catalog 事实派生，外部 Agent 加得了、删不掉。**
   否则它可以写出 `obligations=[]` 让门禁自动通过。
2. **Concept 只承载知识身份与逻辑资源指针**，「用哪个检索后端」是 Binding 的属性。

## 命令

| 命令 | 作用 |
|---|---|
| `unistile add <文件>` | 纳入新文档：落 Resource → 生成 Concept（自动算 sha256）→ 校验 → 建索引 |
| `unistile validate` | L0/L1/L2 + 值级往返校验；未通过不允许进 Catalog |
| `unistile ingest` | 校验 → 归一化文本平面 → Catalog → Provider bind → 重建 index.md 与投影 |
| `unistile install-skills` | 把技能铺到本机各 harness 的 skills 目录 |
| `unistile resolve "<名字>"` | 身份解析 → uid；**库里没有这个实体则 exit 3**，附确定性近似候选 |
| `unistile tree [投影] [--node ID]` | 逐层导航，含 omission 统计 |
| `unistile where <uid>` | 这个 Concept 出现在哪些投影下 |
| `unistile outline <uid>` | 文档的 section 导航图（不检索，Provider 无关） |
| `unistile providers` / `bindings` | Provider 能力声明 / Binding 的 role、stale 状态 |
| `unistile ask` | 受控范围检索 + 回读校验 + Evidence Envelope（**无状态，没有门禁**） |
| `unistile turn start/show/act/answer/abstain` | 一轮问答：义务派生 → 证据校验 → answer 门禁 |
| `unistile turn auto` | 规则驱动器跑完整轮（回归基线用，不做语义判断） |

`ask` 默认沿已登记的 `amends`/`supersedes` 关系有界展开，`--no-hop` 关闭 ——
对比这两种输出就能看到 12 个月和 24 个月的差别。

身份解析只看 Catalog 的 `uid / external_id / aliases / title`，**不碰正文**。
所以 `title` 不可读（UUID、编号）的文档必须在入库时给 `--alias`，否则按名字永远找不到——
这不是检索质量问题，是寻址问题。

`turn` 各字段的含义见 [`AGENTS.md`](AGENTS.md) 与 `skills/unistile-answer/reference/packet.md`。

## 作为 Skill 使用

`skills/` 下两个技能，任何支持 Agent Skills 的 harness 都能装：

| 技能 | 职责 | 触发 |
|---|---|---|
| `unistile-answer` | 在证据门禁下回答问题 | 从受控文档集回答，答错有代价 |
| `unistile-author` | 文档入库、写/修 Concept | 新文档、frontmatter 报错 |

不支持 skill 的 harness：`AGENTS.md` 就是完整说明，或把 SKILL.md 去掉 frontmatter
拼进 system prompt。`reference/` 是按需加载的补充材料，不要一股脑塞进去 ——
它们存在就是为了让主文档保持短。

**自检**：让 agent 回答「A-1007 的质保期是多久？」。**它应该在第一次 answer 时被拦住**，
去读补充协议后才答出 24 个月。直接答 12 个月说明技能没生效。

## 文档解析：anydoc

非 Markdown 文档一律经 [firecrawl/anydoc](https://github.com/firecrawl/anydoc)
转成 GFM Markdown 再进归一化文本平面（纯 Rust，无 ML 模型、无外部服务、无 API key）。
平面本来就以 Markdown 为坐标系，所以 outline、offset、回读、locator 一行都不用改。
`.md` / `.txt` 不经 anydoc（送进去反而会把 `#` 转义）。

**换抽取器就是换坐标系**，因此版本号进 `extractor_version`（`anydoc-0.2.3+normalizer-v1`），
老 locator 靠内容哈希键控的目录继续可读，新旧并存。

**anydoc 给不了页边界**，所以它抽出的资源 `pages=[]`、`locator.page` 缺省 ——
对 PDF 谎称 page=1 比不给更糟。

抽取失败按原因分类（加密 / 缺部件 / 结构损坏 / 超安全上限 / 需要 OCR）抛 `ExtractionFailed`；
`ingest` 跳过这一份并记下原因，**不让整个 bundle 失败**，对应 Obligation 判 blocked。

## 代码地图

| 路径 | 职责 |
|---|---|
| `unistile/spec/` | 字段分层、uid 语法、Schema Registry、validator |
| `unistile/resources/` | anydoc 抽取层 + 归一化文本平面（跨 Provider 的 locator 基准） |
| `unistile/catalog/` | concepts / concept_edges / resource_revisions / resource_bindings |
| `unistile/evidence/` | Provider 契约与能力声明、注册与路由、local-fts/null/weknora、跨系统校验 |
| `unistile/turn/obligations.py` | 义务派生：L1 结构派生（Catalog 事实）+ L3 兜底 |
| `unistile/turn/ledger.py` | unseen/candidate/supported/blocked 状态机 + answer 门禁 |
| `unistile/turn/manifest.py` | Local Navigation Manifest：coverage_hints、成本、省略与 cursor |
| `unistile/turn/session.py` `driver.py` | 轮次状态与合法动作 / RuleSelector 回归基线 |
| `unistile/install.py` | 把技能铺到各 harness |
| `unistile/envelope.py` `indexgen.py` `projections.py` | Evidence Envelope / index.md 生成 / 多视图投影 |

## 已实现 / 未实现

**已实现**：Profile 校验门禁；多投影导航树（同一 Concept 出现在业务/资料/时效三个视图，
后两个规则派生，新文档自动收录）；关系边元数据（`amends` 记录被改的条款）；
归一化文本平面与精确回读；Catalog 与多 Provider Binding（role: primary/shadow/retired）；
多格式解析（anydoc：docx/pptx/xlsx/odf/rtf/epub/csv/pdf）；local-fts（FTS5 trigram，中文可用）；
跨系统校验（scope 越界 / binding stale / locator 哈希漂移 / 能力诚实）；Evidence Envelope；
Turn Runtime（义务从 Catalog 事实派生、四状态机、多维预算、answer/qualified/abstain 三出口门禁、
状态持久化与 Trace）；Local Navigation Manifest（逐层展开、coverage_hints、字符成本、
省略公开与分页）；整轮控制流基线（RuleSelector + 5 条 golden 轨迹）；
聚合门槛（边元数据 clause → 被点名条款必须在场；`amends` 两端都要读；够不着时开轮即 blocked）。

**未实现**（能力缺失均由 `capabilities()` 显式声明）：页边界；扫描版 PDF（需 OCR）；
向量与混合检索、rerank/MMR、图片理解；WeKnora 实际接入；
**义务的语义门槛** —— 来源、条数、来源数、被点名条款都能查，
「这段话是否回答了问题」查不了，见 `semantic_gap`；
L2 任务形状义务（compare/汇总类）；跨轮 Session State 与指代消解；
Value Vector 与有界 Hopping；dependency-directed freshness。

## 证据边界

本 POC 验证的是**契约与控制流**，不是检索质量。golden set 只有 8 条、语料只有 3 篇文档，
`core recall@3 = 6/6` 不能外推到真实知识库。known_limitation 的 2 条记录了 keyword-only
的能力边界，接入向量 Provider 后应当翻转。
