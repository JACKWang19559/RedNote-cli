"""Validate copied Xiaohongshu URLs without navigating or resolving links."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .errors import RedNoteError

_ID = re.compile(r"[0-9a-fA-F]{24}\Z")
_NOTE_PATH = re.compile(r"/(?:explore|discovery/item)/([0-9a-fA-F]{24})/?\Z")
_USER_PATH = re.compile(r"/user/profile/([0-9a-fA-F]{24})/?\Z")
_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com"}


def parse_reference(value: str, kind: str = "note", xsec_token: str | None = None) -> tuple[str, str]:
    """Return the ID and unescaped-once token from a full URL or an explicit ID.

    An explicit token overrides the URL's token, allowing stale copied links to
    be refreshed. Short share links are deliberately not resolved over HTTP.
    """
    if kind not in {"note", "user", "profile"}:
        raise RedNoteError("INVALID_REFERENCE_KIND", "引用类型必须是 note 或 user。")
    if not isinstance(value, str) or not value.strip():
        raise RedNoteError("INVALID_REFERENCE", "请提供笔记/用户 ID 或完整小红书链接。")
    value = value.strip()
    if _ID.fullmatch(value):
        identifier = value
    else:
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in _HOSTS
                and parsed.port in {None, 443}
                and parsed.username is None
                and parsed.password is None
                and not any(c.isspace() for c in value)
            )
        except ValueError:
            valid = False
        if not valid:
            raise RedNoteError(
                "INVALID_REFERENCE", "请使用完整的 https://www.xiaohongshu.com 链接或 24 位 ID。",
                "xhslink.com 短链接请先在浏览器中打开，再复制完整地址。",
            )
        match = (_NOTE_PATH if kind == "note" else _USER_PATH).fullmatch(parsed.path)
        if match is None:
            raise RedNoteError("INVALID_REFERENCE", "小红书链接路径与请求的笔记/用户类型不匹配。")
        identifier = match.group(1)
        if xsec_token is None:
            tokens = parse_qs(parsed.query, keep_blank_values=True).get("xsec_token", [])
            if len(tokens) > 1:
                raise RedNoteError("INVALID_REFERENCE", "链接包含重复的 xsec_token，请提供单一有效令牌。")
            xsec_token = tokens[0] if tokens else None
    if not isinstance(xsec_token, str) or not xsec_token.strip():
        raise RedNoteError(
            "MISSING_XSEC_TOKEN", "读取笔记和用户主页需要 xsec_token。",
            "使用搜索结果中的 xsecToken 配合 --xsec-token，或提供包含 xsec_token 的完整链接。",
        )
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in xsec_token):
        raise RedNoteError("INVALID_XSEC_TOKEN", "xsec_token 不能包含空白或控制字符。")
    return identifier, xsec_token
