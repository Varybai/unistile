---
name: unistile-author
description: Add documents to a unistile knowledge bundle and author Concept files that pass the unistile OKF Profile v1 validator — OKF tightened from one required frontmatter key to five, with a registered uid grammar, six registered relation types, and backend keys banned from Concepts. Use when ingesting a new document (docx, pdf, xlsx, md…) into a knowledge base, writing or fixing Concept frontmatter, or when unistile validate reports errors.
license: MIT
metadata:
  tags: "OKF, knowledge, ingestion, schema, validation"
  category: "knowledge"
---

# unistile-author

把文档纳入 OKF Bundle。**你提候选，脚本执行校验**——不要手写 Concept 文件再祈祷它合法。

## 先确认环境

```bash
unistile --help >/dev/null 2>&1 && echo OK || echo "未安装"
```

未安装：`pip install git+https://github.com/Varybai/unistile.git`
（本技能目录不是项目目录，别在这里找源码。）

## 纳入一份新文档

```bash
unistile add <文件路径> \
  --uid "kn:<namespace>:<local_id>[:<qualifier>]" \
  --title "<人读得懂的标题>" \
  --domain <domain> \
  --description "<一句话，会进 index.md 的条目>" \
  --relation "<type>:<target_uid>"        # 可重复
```

它做的事：拷进 `assets/` → 算 sha256 → 生成 Concept frontmatter → **校验** →
不通过就删掉刚写的文件回滚 → 通过则 `ingest`（归一化、建索引、重建 index.md 与投影）。

支持 17 种后缀：`.md .markdown .txt` 直接进；
`.docx .doc .docm .pptx .ppt .xlsx .xls .odt .ods .odp .rtf .epub .csv .pdf` 经 anydoc 转 GFM。

## uid 语法

```
kn:<namespace>:<local_id>[:<qualifier>]
```

| 位置 | 规则 |
|---|---|
| `namespace` | 必须已登记：`agreement` `equipment` `spec` `policy` `project` `organization` `workflow` `concept` |
| `local_id` | `[A-Za-z0-9][A-Za-z0-9._-]{0,96}`，业务上稳定的编号，别用文件名 |
| `qualifier` | 同一实体的从属文档，如 `kn:equipment:A-1007:acceptance-spec` |

整串上限 200 字符。namespace 不在表里会被拒——**要新的就先改
`unistile/spec/schema_registry.py`，不要绕过**。

## 一个 Concept 长什么样

```yaml
---
type: Knowledge Concept          # L0 必填
title: AGR0048 主设备采购协议      # L0 必填
status: stable                   # L0 必填：draft | stable | deprecated | superseded
uid: "kn:agreement:AGR0048"      # L1 必填
evidence_class: document         # L1 必填：document | structured | computation | image | code
description: 主设备采购的商务与质量条款
media_type: text/markdown
resource: asset://documents/AGR0048-v3.md
resource_revision: 3
sha256: "sha256:…"
relations:
  - type: amends
    target: "kn:agreement:AGR0048"
    metadata: {clause: "7.2", effective_from: "2026-07-15"}
---

正文（可选）。检索证据来自 `resource` 指向的原始文档，不是这里。
```

## 关系类型只有六种

`amends` `supersedes` `applies_to` `part_of` `contract_for` `references`

写别的会被拒。`metadata` 是治理事实，**会被运行时当硬约束用**——
`amends` 上的 `clause: "7.2"` 会变成「回答前必须读到 7.2 那一节」。
知道改的是哪一条就写上，这不是装饰。

## 绝对不能出现在 Concept 里的键

```
access  binding  binding_key  binding_id  provider  provider_id  backend
backend_object_id  knowledge_id  knowledge_ids  embedding_model  chunk_size
indexed_sha256
```

原因：Concept 是 canonical 知识身份，**不能知道后端是谁**。
后端信息属于 `resource_bindings` 表和 Provider Registry。
把 `access: {method: weknora}` 写进 Concept，热插拔当场失效。

## 校验

```bash
unistile validate        # L0 语法 / L1 字段与引用 / L2 全局唯一性与投影
unistile ingest          # 校验不过就拒绝写入 Catalog
unistile bindings        # 看 role / status / stale
```

`ingest` 跳过某份文档并给出 `ExtractionFailed` 时，说明那份文件本身抽不出内容
（加密、结构损坏、扫描版 PDF 需 OCR）——**不是整个 bundle 失败**，其余照常入库。

## 校验报错时

读 `reference/profile.md` —— 每个错误码（`L0.*` / `L1.*` / `L2.*`）的含义、
字段分层全表、以及"一份文档能抽出几个 Concept"都在里面。不要靠猜。
