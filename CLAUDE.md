# unistile

Claude Code 不读 `AGENTS.md`，所以这里把它导入进来。**正文只有一份，在 AGENTS.md**，
改内容改那边，别在这里复制一份出来漂移。

@AGENTS.md

## Claude Code 专属

技能已经就位：仓库里的 `.claude/skills/` 是指向 `skills/` 的符号链接，克隆下来即可用。
装到用户级（所有项目可用）：

```bash
unistile install-skills
```

| 技能 | 什么时候会触发 |
|---|---|
| `unistile-answer` | 从受控文档集回答问题，答错有代价 |
| `unistile-author` | 新文档入库、写/修 Concept、`unistile validate` 报错 |

技能里的 `reference/` 是按需加载的补充材料，不要主动全读进来——它们存在就是为了
让 SKILL.md 保持短。

## 改这个仓库本身时

```bash
python -m pytest -q          # 改 turn/ 或 spec/ 之后必跑
```

`runtime/` 是派生物，可以整个删掉重建（`unistile ingest`），不要手改、不要提交。
