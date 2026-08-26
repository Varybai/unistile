# unistile OKF Profile v1

上游 OKF 只强制 `type`。本 Profile 收紧到 5 个必填，**单向兼容**：
本 Bundle 是合法的 OKF，上游 Bundle 过不了本 validator。

## 字段分层

| 层 | 字段 | 说明 |
|---|---|---|
| L0 必填 | `type` `title` `status` | 上游语义 + 状态机 |
| L1 必填 | `uid` `evidence_class` | 身份与证据类型 |
| L1 可选 | `external_id` `sha256` `aliases` `relations` `media_type` `resource_revision` | |
| 禁止 | 见 SKILL.md 的禁用键表 | 后端信息不进 Concept |

`status`：`draft` `stable` `deprecated` `superseded`
`evidence_class`：`document` `structured` `computation` `image` `code`

`evidence_class: structured` 的 Concept 没有可回读原文，
基于它的义务会在开轮时直接 `blocked` —— 这是正确行为，不是缺陷。

## 校验错误码

| 码 | 含义 |
|---|---|
| `L0.parse` | frontmatter 不是合法 YAML |
| `L0.required` | 缺 L0 必填字段 |
| `L0.status` | status 不在枚举内 |
| `L0.roundtrip` | 值级往返不一致（引号、数字、布尔被 YAML 改写） |
| `L1.required` | 缺 L1 必填字段 |
| `L1.evidence_class` | evidence_class 不在枚举内 |
| `L1.uid_syntax` | uid 不符合 `kn:<ns>:<local>[:<qual>]` |
| `L1.uid_namespace` | namespace 未登记 |
| `L1.relation_type` | 关系类型未登记 |
| `L1.relation_target` | 关系指向不存在的 uid |
| `L1.relation_metadata` | `metadata` 不是映射 |
| `L1.resource_revision` | revision 不是正整数 |
| `L1.sha256_mismatch` | 声明的 sha256 与文件实际不符 |
| `L1.resource_missing` | `resource` 指向的文件不存在 |
| `L2.forbidden` | 出现禁用键 |
| `L2.uid_unique` | uid 重复 |
| `L2.projection*` | 投影定义有重复、环、悬空引用 |

## index.md 不要手改

按上游 SPEC §8 自动生成，`unistile ingest` 每次重建。
条目格式 `* [Title](relative-url) - description`，description 取自 Concept 的 `description`。
只有 bundle 根的 index.md 带 `okf_version: "0.2"`。
缺 index.md 不算错误——上游明确要求 consumers 不得因此拒绝 bundle。

## 投影

`knowledge/projections/*.yaml` 是**手写**投影（materialized）。
`document-collection` 和 `lifecycle` 是**规则派生**（query_backed），
新文档自动收录，不用改任何 YAML。

想让新文档自动进某个视图，就把它做成规则派生的；
手写投影只在规则算不出来时用（如按项目/供应商分组）。

## 一份文档能抽出几个 Concept

**能抽多个。** 一个 Concept = 一个稳定的知识身份，不是一个文件。

```
A-1007 主设备         kn:equipment:A-1007                    structured（无原文）
  验收规范            kn:equipment:A-1007:acceptance-spec     document
  维护手册            kn:equipment:A-1007:maintenance-manual  document
```

两个 Concept 指向同一份文件时，各自会被完整索引一遍——
`BindRequest` 目前不带字符区间，做不到只索引其中一段。
真要切分，先把文件拆开。
