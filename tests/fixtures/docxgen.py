"""最小 OOXML 生成器 —— 造一份带真实标题样式的 .docx，不引入 python-docx。

只用于测试夹具。样式必须在 styles.xml 里声明 outlineLvl，
否则 anydoc 认不出标题（只有 w:pStyle 名字不够）。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'

_CT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.styles+xml"/></Types>'
)
_RELS = (
    f'<?xml version="1.0" encoding="UTF-8"?><Relationships {R}><Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
_DRELS = (
    f'<?xml version="1.0" encoding="UTF-8"?><Relationships {R}><Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/></Relationships>'
)
_STYLES = (
    f'<?xml version="1.0" encoding="UTF-8"?><w:styles {W}>'
    + "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/>'
        f'<w:pPr><w:outlineLvl w:val="{i - 1}"/></w:pPr></w:style>'
        for i in range(1, 7)
    )
    + "</w:styles>"
)


def _para(text: str, level: int | None) -> str:
    pr = (
        f'<w:pPr><w:pStyle w:val="Heading{level}"/><w:outlineLvl w:val="{level - 1}"/></w:pPr>'
        if level
        else ""
    )
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<w:p>{pr}<w:r><w:t xml:space="preserve">{esc}</w:t></w:r></w:p>'


def write_docx(path: str | Path, blocks: list[tuple[str, int | None]]) -> Path:
    """blocks: [(文本, 标题级别 or None)]"""
    body = "".join(_para(t, lvl) for t, lvl in blocks)
    doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{body}</w:body></w:document>'
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/_rels/document.xml.rels", _DRELS)
    return p


MAINTENANCE_BLOCKS = [
    ("A-1007 维护服务协议（第 1 版）", 1),
    ("本协议规定 A-1007 主设备在质保期届满后的维护责任与响应时限。", None),
    ("1. 响应时限", 2),
    ("供应商应在接到故障通知后 4 小时内响应，24 小时内到场。", None),
    ("2. 备件供应", 2),
    ("关键备件供应商应保持不少于 2 套的安全库存，交付周期不超过 15 日。", None),
    ("3. 服务费用", 2),
    ("年度维护服务费为人民币 18 万元，按季度支付。", None),
]
