# OKF Skills

两个技能，按职责分开——它们的触发条件和内容都不一样，合成一个会让 description 变模糊。

| 技能 | 干什么 | 什么时候触发 |
|---|---|---|
| `unistile-answer` | 在证据门禁下回答问题 | 需要从受控文档集回答，且答错有代价 |
| `unistile-author` | 把文档纳入知识库、写/修 Concept | 新文档入库、frontmatter 报错 |

两者都依赖 `unistile` 命令行在 PATH 上：

```bash
cd unistile && uv pip install -e .
```

## 装到各个 harness

| Harness | 位置 |
|---|---|
| Claude Code（用户级） | `cp -r skills/* ~/.claude/skills/` |
| Claude Code（项目级） | `cp -r skills/* .claude/skills/` |
| Cursor | `cp -r skills/* .cursor/skills/` |
| Codex / 无 skill 支持的 | 见下 |

一条命令装全（按需改目标目录）：

```bash
for d in ~/.claude/skills .cursor/skills; do mkdir -p "$d" && cp -r skills/unistile-* "$d/"; done
```

## 没有 skill 支持的 harness

SKILL.md 去掉 YAML frontmatter 就是一份普通指令文档。`AGENTS.md`（Codex 原生读）
已经是 `unistile-answer` 的精简版；要完整的就把 SKILL.md 正文拼进 system prompt：

```bash
sed '1{/^---$/,/^---$/d}' skills/unistile-answer/SKILL.md > /tmp/unistile-answer.txt
```

`reference/` 下的文件是**按需加载**的补充材料，不要一股脑塞进 system prompt——
它们的存在就是为了让主文档保持短。

## 自检

装完之后让 agent 跑这一轮，它应该在第一次 answer 时被拦住：

```
用 unistile 回答：A-1007 的质保期是多久？
```

预期行为：`turn start` → 发现两条义务 → 读原协议 7.2（12 个月）→
试图回答被门禁拒（exit 3，`obl-amendments` 未满足）→ 去读补充协议 → 答 24 个月。

如果它直接答了 12 个月，说明技能没生效或者它绕开了 `turn` 直接读文件。
