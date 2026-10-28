"""Validate publication requests before handing them to xiaohongshu-mcp.

The wire fields and schedule window follow upstream ``service.go``. Title
length follows ``pkg/xhsutil/title.go``: ASCII UTF-16 units weigh one, other
units weigh two, and the sum is rounded up after dividing by two. The creator
page remains authoritative for content length, media formats, and account
capabilities. This module never uploads files or fetches image URLs.
"""

from __future__ import annotations

import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from .errors import RedNoteError

VISIBILITIES = ("公开可见", "仅自己可见", "仅互关好友可见")
_COMMON_FIELDS = {"title", "content", "tags", "schedule_at", "visibility", "products"}
_RFC3339 = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z"
)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"[A-Za-z]:")


def _text(value: Any, name: str, *, multiline: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RedNoteError("INVALID_PUBLISH", f"{name} 必须是非空文本。")
    allowed_controls = "\t\r\n" if multiline else ""
    if any(
        (ord(char) < 32 and char not in allowed_controls)
        or ord(char) == 127
        or 0xD800 <= ord(char) <= 0xDFFF
        or (not multiline and char in "\u0085\u2028\u2029")
        for char in value
    ):
        raise RedNoteError(
            "INVALID_PUBLISH", f"{name} 包含不支持的控制字符或 Unicode 字符。"
        )
    return value


def title_length(title: str) -> int:
    """Return the upstream weighted UTF-16 title length (an emoji weighs 2)."""
    weight = sum(
        1 if ord(char) <= 127 else 2 if ord(char) <= 0xFFFF else 4 for char in title
    )
    return (weight + 1) // 2


def _strings(values: Any, name: str, *, tags: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise RedNoteError("INVALID_PUBLISH", f"{name} 必须是文本列表。")
    result: list[str] = []
    for value in values:
        value = _text(value, name).strip()
        if tags:
            value = value.lstrip("#").strip()
            if not value:
                raise RedNoteError("INVALID_PUBLISH", "话题标签不能只包含 #。")
        if value not in result:
            result.append(value)
    if tags and len(result) > 10:
        raise RedNoteError(
            "INVALID_PUBLISH", "最多支持 10 个不同话题标签，避免后端静默截断。"
        )
    return result


def _media_text(value: Any, *, allow_url: bool) -> tuple[str, bool]:
    value = _text(value, "媒体路径或 URL")
    # Do not strip paths: spaces can be meaningful in actual filenames.
    is_scheme = bool(_SCHEME.match(value)) and not _WINDOWS_DRIVE.match(value)
    if is_scheme:
        try:
            parsed = urlsplit(value)
            valid = (
                allow_url
                and parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.port != 0
                and not any(char.isspace() for char in value)
                and "\\" not in value
            )
        except ValueError:
            valid = False
        if not valid:
            raise RedNoteError(
                "INVALID_MEDIA",
                "图片 URL 必须为不含凭证的 HTTP(S) 地址；视频只支持文件路径。",
            )
        return value, True
    return value, False


def _schedule(value: Any) -> datetime:
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise RedNoteError(
            "INVALID_SCHEDULE",
            "定时时间需要带时区的 RFC3339 格式，例如 2026-10-29T10:00:00+08:00。",
        )
    try:
        parsed = datetime.fromisoformat(
            value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
        )
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise RedNoteError(
            "INVALID_SCHEDULE", "定时时间不是有效的日期、时间或时区。"
        ) from None


def validate_publish_payload(payload: dict, kind: str = "image") -> dict:
    """Return a normalized wire payload without I/O or clock-dependent checks.

    Backend filesystem paths are preserved, including paths not present on the
    CLI machine. The builder below separately checks local files and the
    schedule window. Unknown fields are rejected instead of silently dropped.
    """
    if not isinstance(kind, str) or kind not in {"image", "video"}:
        raise RedNoteError("INVALID_PUBLISH", "发布类型必须为 image 或 video。")
    if not isinstance(payload, dict):
        raise RedNoteError("INVALID_PUBLISH", "发布请求必须是 JSON 对象。")
    allowed = _COMMON_FIELDS | (
        {"images", "is_original"} if kind == "image" else {"video"}
    )
    if set(payload) - allowed:
        raise RedNoteError("INVALID_PUBLISH", "发布请求包含当前类型不支持的字段。")
    title = _text(payload.get("title"), "标题").strip()
    if title_length(title) > 20:
        raise RedNoteError(
            "INVALID_PUBLISH",
            "标题超过 20 字：ASCII 字符计半字，普通中文计一字，非 BMP 表情计两字。",
        )
    result = {
        **payload,
        "title": title,
        "content": _text(payload.get("content"), "正文", multiline=True),
    }
    if "visibility" in result and result["visibility"] not in VISIBILITIES:
        raise RedNoteError(
            "INVALID_PUBLISH", "可见范围必须为公开可见、仅自己可见或仅互关好友可见。"
        )
    for name in ("tags", "products"):
        if name in result:
            result[name] = _strings(result[name], name, tags=name == "tags")
    if "schedule_at" in result:
        _schedule(result["schedule_at"])
    if kind == "image":
        images = result.get("images")
        if not isinstance(images, (list, tuple)) or not images:
            raise RedNoteError(
                "INVALID_MEDIA", "图文发布至少需要一张图片，images 必须是列表。"
            )
        result["images"] = [_media_text(value, allow_url=True)[0] for value in images]
        if "is_original" in result and not isinstance(result["is_original"], bool):
            raise RedNoteError("INVALID_PUBLISH", "is_original 必须为布尔值。")
    else:
        result["video"] = _media_text(result.get("video"), allow_url=False)[0]
    return result


def _resolve_media(value: str, *, allow_url: bool, server_paths: bool) -> str:
    value, is_url = _media_text(value, allow_url=allow_url)
    if is_url:
        return value
    if server_paths:
        if not (
            PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
        ):
            raise RedNoteError(
                "INVALID_MEDIA", "--server-paths 需要后端机器上的绝对文件路径。"
            )
        return value
    try:
        path = Path(value).expanduser().resolve(strict=True)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            raise ValueError
    except (OSError, ValueError, RuntimeError):
        raise RedNoteError(
            "INVALID_MEDIA",
            "本地媒体必须是存在、可访问的非空普通文件。",
            "远程后端文件请使用 --server-paths 并提供后端绝对路径。",
        ) from None
    return str(path)


def build_publish_payload(
    title,
    content,
    *,
    images=None,
    video=None,
    tags=None,
    schedule_at=None,
    is_original=False,
    visibility="公开可见",
    products=None,
    server_paths=False,
    now=None,
) -> dict:
    """Build a verified image/video request; no uploads or external mutations.

    ``server_paths`` preserves absolute Windows or POSIX backend paths without
    inspecting them locally. ``now`` may be an aware datetime for deterministic
    tests; real calls use the current UTC time.
    """
    if not isinstance(server_paths, bool) or not isinstance(is_original, bool):
        raise RedNoteError(
            "INVALID_PUBLISH", "server_paths 和 is_original 必须为布尔值。"
        )
    kind = "video" if video is not None else "image"
    if kind == "video" and (images is not None or is_original):
        raise RedNoteError("INVALID_PUBLISH", "视频发布不能包含图片或图文原创声明。")
    payload = {"title": title, "content": content, "visibility": visibility}
    payload["video" if kind == "video" else "images"] = (
        video if kind == "video" else images
    )
    if is_original:
        payload["is_original"] = True
    for name, value in (
        ("tags", tags),
        ("products", products),
        ("schedule_at", schedule_at),
    ):
        if value is not None:
            payload[name] = value
    result = validate_publish_payload(payload, kind=kind)
    if schedule_at is not None:
        scheduled = _schedule(schedule_at)
        if now is None:
            now = datetime.now(timezone.utc)
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise RedNoteError("INVALID_SCHEDULE", "用于校验的当前时间必须带时区。")
        try:
            difference = scheduled.astimezone(timezone.utc) - now.astimezone(
                timezone.utc
            )
        except (OverflowError, ValueError):
            raise RedNoteError(
                "INVALID_SCHEDULE", "定时时间无法换算为有效 UTC 时间。"
            ) from None
        if not timedelta(hours=1) <= difference <= timedelta(days=14):
            raise RedNoteError(
                "INVALID_SCHEDULE", "定时发布时间必须在当前时间的 1 小时至 14 天内。"
            )
    if kind == "image":
        result["images"] = [
            _resolve_media(value, allow_url=True, server_paths=server_paths)
            for value in result["images"]
        ]
    else:
        result["video"] = _resolve_media(
            result["video"], allow_url=False, server_paths=server_paths
        )
    return result
