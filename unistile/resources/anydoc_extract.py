"""anydoc 抽取层 —— docx/pptx/xlsx/odf/rtf/epub/csv/pdf → GFM Markdown。

为什么接它：归一化文本平面本来就以 Markdown 为坐标系（outline 由 `#` 标题算出，
char offset 落在同一串文本上）。anydoc 输出 GFM，正好接在平面之前，
后面的 outline、offset、回读、locator 全部不用改。

它给不了什么，必须说清楚：**没有页边界**。因此 anydoc 抽出的资源
`pages=[]`、`locator.page` 缺省 —— 对 PDF 谎称 page=1 比不给更糟。

版本号必须进 extractor_version：换抽取器 = 换坐标系，
老 locator 靠内容哈希键控的目录继续可读，新旧并存。
"""

from __future__ import annotations

from pathlib import Path

try:  # pragma: no cover - 环境差异
    import anydoc as _anydoc
    from importlib.metadata import version as _pkg_version

    VERSION = _pkg_version("firecrawl-anydoc")
    AVAILABLE = True
except Exception as _e:  # pragma: no cover
    _anydoc = None
    VERSION = "unavailable"
    AVAILABLE = False
    _IMPORT_ERROR = _e


class ExtractionFailed(ValueError):
    """文档存在但抽不出内容：加密、结构损坏、超出安全上限、需要 OCR。

    与 UnsupportedMediaType 分开 —— 那个说"这类文件我们不处理"，
    这个说"这类文件我们处理，但这一份不行"。对应的 Obligation 应判 blocked。
    """


# 后缀 → media_type。anydoc 支持什么，这里就登记什么；不在表里的一律拒绝。
MEDIA_TYPES: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".rtf": "application/rtf",
    ".epub": "application/epub+zip",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
}

SUPPORTED_MEDIA_TYPES = frozenset(MEDIA_TYPES.values())


def media_type_for(path: str | Path) -> str | None:
    return MEDIA_TYPES.get(Path(path).suffix.lower())


def to_markdown(path: str | Path) -> str:
    if not AVAILABLE:  # pragma: no cover
        raise ExtractionFailed(
            f"firecrawl-anydoc 不可用（{_IMPORT_ERROR}）；"
            " md/txt 仍可处理，其余格式的 Obligation 应判 blocked，不得静默降级"
        )
    p = str(path)
    try:
        return _anydoc.to_markdown(p)
    except _anydoc.EncryptedError as e:
        raise ExtractionFailed(f"文档加密或有密码保护：{p}（{e}）") from e
    except _anydoc.UnsupportedError as e:
        # 扫描版/纯图 PDF 走到这里 —— anydoc 明确声明不做 OCR
        raise ExtractionFailed(f"anydoc 无法转换：{p}（{e}）；扫描版 PDF 需要 OCR，anydoc 不做") from e
    except _anydoc.MissingPartError as e:
        raise ExtractionFailed(f"文档缺少必需部件：{p}（{e}）") from e
    except _anydoc.MalformedError as e:
        raise ExtractionFailed(f"文档结构损坏，无可提取内容：{p}（{e}）") from e
    except _anydoc.ResourceLimitError as e:
        raise ExtractionFailed(f"超出 anydoc 安全上限：{p}（{e}）") from e
    except _anydoc.ConvertError as e:
        raise ExtractionFailed(f"anydoc 转换失败：{p}（{type(e).__name__}: {e}）") from e
