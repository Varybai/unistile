# OKF Skills

两个技能，按职责分开——它们的触发条件和内容都不一样，合成一个会让 description 变模糊。

| 技能 | 干什么 | 什么时候触发 |
|---|---|---|
| `unistile-answer` | 在证据门禁下回答问题 | 需要从受控文档集回答，且答错有代价 |
| `unistile-author` | 把文档纳入知识库、写/修 Concept | 新文档入库、frontmatter 报错 |

两者都依赖 `unistile` 命令行在 PATH 上。

## 装到各个 harness

```bash
pip install git+https://github.com/Varybai/unistile.git
unistile install-skills
```

`install-skills` 只往**已经装了的** harness 写（marker 目录存在才动），
整目录替换而不是增量合并，所以重跑一次就是升级：

| Harness | 目标目录 |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |
| Cursor | `~/.cursor/skills/` |
| Grok | `~/.grok/skills/`（也会读 `~/.claude/skills/`） |
| Pi | `~/.pi/agent/skills/` |
| OMP | `~/.omp/agent/skills/` |
| Windsurf | `~/.windsurf/skills/` |

`--dry-run` 先看会写到哪；`--all` 不管装没装全铺一遍；`--dest <目录>` 指定单个目标
（项目级就用 `--dest .claude/skills`）。

从源码 checkout 跑也可以，`install.py` 找不到打包进来的 `unistile/_skills`
就会退回仓库根的 `skills/`。

## 没有 skill 支持的 harness

SKILL.md 去掉 YAML frontmatter 就是一份普通指令文档。仓库根的 `AGENTS.md`
（Codex 等原生读）本身就是完整说明，`CLAUDE.md` 用 `@AGENTS.md` 导入同一份正文。
要把技能正文拼进 system prompt：

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
