#!/usr/bin/env python3
"""给已有 Bundle 回填 aliases —— 这是**示例脚本，不是 Runtime 能力**。

为什么不在 `unistile ingest` 里自动做：
从文档里认出「哪个字符串是这个人的名字」是语义判断。Runtime 的纪律是零语义判断——
它只做集合运算和确定性校验。猜错名字会让一份文档被错误地寻址到另一个人身上，
这比找不到严重得多。所以姓名由作者用 `unistile add --alias` 声明，
批量补录就用这种一次性脚本，**产物必须人工过目**。

典型场景：文档批量入库时 title 用了 UUID（`HRBoost 候选人简历 64f41f68 / 300dab39`），
于是 `unistile resolve "马德旺"` 永远找不到人——不是检索质量问题，是寻址问题。

用法：

    python examples/backfill_aliases.py --bundle knowledge --dry-run
    python examples/backfill_aliases.py --bundle knowledge --apply
    unistile ingest        # 改完必须重建索引，aliases 才进 Catalog

默认只打印建议（--dry-run）。--apply 才写文件。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 中文姓名的粗略形状：2-4 个汉字。宁可漏，不可错——漏了人工补，错了会污染寻址。
CJK_NAME = re.compile(r"^[一-龥]{2,4}$")
LATIN_NAME = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}$")

# 「姓名：崔琰 性别：女」——必须在空白处停下，否则会把后面的「性别」一起吞掉
LABELLED_NAME = re.compile(
    r"(?:姓\s*名|名\s*字|Name)\s*[:：]\s*([一-龥]{2,4}|[A-Za-z]+(?: [A-Za-z]+){0,2})"
)

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
RESOURCE_LINE = re.compile(r'^resource:\s*"?([^"\n]+)"?', re.M)


# 简历里长得像名字的小标题。不排掉的话「个人信息」「教育经历」会被当成人名写进 aliases，
# 而错误的别名比缺失的别名危险得多——它会把查询静默地指到错的人身上。
SECTION_WORDS = {
    "个人信息", "基本信息", "个人简介", "个人优势", "自我评价", "求职意向", "期望职位",
    "教育经历", "教育背景", "工作经历", "工作经验", "项目经历", "项目经验", "实习经历",
    "专业技能", "技能特长", "技能清单", "荣誉奖项", "获奖情况", "证书", "简历", "履历",
    "联系方式", "语言能力", "培训经历", "社会实践", "兴趣爱好", "附件", "备注",
}


def _looks_like_name(s: str) -> bool:
    if s in SECTION_WORDS:
        return False
    return bool(CJK_NAME.match(s) or LATIN_NAME.match(s))


def candidate_names(text: str, *, head_chars: int = 500) -> list[str]:
    """从正文开头提名字候选。只看开头——姓名几乎总在最前面，往后翻只会引入噪声。

    三种形状（按可信度排序）：
      1. `姓名：崔琰`             有标签，最可信
      2. `# 崔琰_AI 算法工程师`   H1 标题，取第一个分隔段
      3. `# 赵先生`               H1 就是名字本身
    """
    head = text[:head_chars]
    found: list[str] = []

    def add(name: str) -> None:
        name = name.strip()
        if name and _looks_like_name(name) and name not in found:
            found.append(name)

    for m in LABELLED_NAME.finditer(head):
        add(m.group(1))

    for line in head.splitlines()[:10]:
        line = line.strip()
        # 只认 H1。H2/H3 是「个人优势」「教育经历」这类小节标题，不是名字。
        if not line.startswith("# "):
            continue
        heading = line[2:].strip()
        add(heading)                                  # 整个标题就是名字
        add(re.split(r"[_\-|/，,]", heading)[0])       # 名字_职位_年限

    return found


def resource_text(bundle: Path, runtime: Path, concept_path: Path) -> str | None:
    """从归一化文本平面取正文。跑过 `unistile ingest` 才有。

    按 `resource:` URI 关联——meta.json 里存的就是它，比绕 sha256 直接。
    """
    m = FRONTMATTER.match(concept_path.read_text(encoding="utf-8"))
    if not m:
        return None
    res = RESOURCE_LINE.search(m.group(1))
    if not res:
        return None
    uri = res.group(1).strip()

    for meta_path in (runtime / "resources").rglob("meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("resource_uri") == uri:
            text_path = meta_path.parent / "text.txt"
            if text_path.exists():
                return text_path.read_text(encoding="utf-8")
    return None


def insert_aliases(concept_text: str, aliases: list[str]) -> str | None:
    """把 aliases 插进 frontmatter。已经有 aliases 的跳过——不覆盖人写的东西。"""
    m = FRONTMATTER.match(concept_text)
    if not m or re.search(r"^aliases:", m.group(1), re.M):
        return None
    line = f"aliases: {json.dumps(aliases, ensure_ascii=False)}\n"
    return concept_text[: m.end(1) + 1] + line + concept_text[m.end(1) + 1 :]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", default="knowledge")
    ap.add_argument("--runtime", default="runtime")
    ap.add_argument("--apply", action="store_true", help="真的写文件；不给就只打印")
    ap.add_argument("--dry-run", action="store_true", help="只打印（默认行为，写出来是为了显式）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几份（0 = 不限）")
    args = ap.parse_args(argv)

    if args.dry_run and args.apply:
        print("--dry-run 和 --apply 不能同时给。", file=sys.stderr)
        return 2

    bundle, runtime = Path(args.bundle), Path(args.runtime)
    if not (runtime / "resources").is_dir():
        print(f"{runtime}/resources 不存在——先跑一次 `unistile ingest`。", file=sys.stderr)
        return 2

    concepts = sorted((bundle / "domains").rglob("concepts/*.md"))
    proposed = skipped = 0

    for path in concepts:
        if args.limit and proposed >= args.limit:
            break
        text = resource_text(bundle, runtime, path)
        if text is None:
            skipped += 1
            continue
        names = candidate_names(text)
        if not names:
            skipped += 1
            continue
        updated = insert_aliases(path.read_text(encoding="utf-8"), names)
        if updated is None:
            skipped += 1
            continue

        proposed += 1
        print(f"{path.relative_to(bundle)}\n    aliases: {names}")
        if args.apply:
            path.write_text(updated, encoding="utf-8")

    print(f"\n{'已写入' if args.apply else '建议'} {proposed} 份，跳过 {skipped} 份"
          f"（共 {len(concepts)}）。")
    if proposed and not args.apply:
        print("确认无误后加 --apply 再跑一遍。")
    if proposed and args.apply:
        print("接着跑 `unistile ingest`，aliases 才会进 Catalog。")
    print("\n提醒：姓名是猜的。逐条过目，错的比缺的更糟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
