"""uid 语法：kn:<namespace>:<local_id>[:<qualifier>]

uid 不可变、不可复用。文件移动、重命名、改标题都不改 uid —— 这是它存在的唯一理由。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LEN = 200

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,96}"
_NAMESPACE = r"[a-z0-9][a-z0-9._-]{0,63}"
UID_RE = re.compile(
    rf"^kn:(?P<namespace>{_NAMESPACE}):(?P<local_id>{_SEGMENT})(?::(?P<qualifier>{_SEGMENT}))?$"
)


class UidError(ValueError):
    pass


@dataclass(frozen=True)
class Uid:
    namespace: str
    local_id: str
    qualifier: str | None = None

    def __str__(self) -> str:
        base = f"kn:{self.namespace}:{self.local_id}"
        return f"{base}:{self.qualifier}" if self.qualifier else base

    @property
    def parent(self) -> "Uid | None":
        """带 qualifier 的治理对象，其上位实体 uid。"""
        if self.qualifier is None:
            return None
        return Uid(self.namespace, self.local_id)


def parse(raw: str) -> Uid:
    if not isinstance(raw, str) or not raw:
        raise UidError("uid 为空")
    if len(raw) > MAX_LEN:
        raise UidError(f"uid 超过 {MAX_LEN} 字符：{raw[:40]}...")
    m = UID_RE.match(raw)
    if not m:
        raise UidError(
            f"uid 语法不合法：{raw!r}；应为 kn:<namespace>:<local_id>[:<qualifier>]"
        )
    return Uid(m["namespace"], m["local_id"], m["qualifier"])


def is_valid(raw: str) -> bool:
    try:
        parse(raw)
    except UidError:
        return False
    return True
