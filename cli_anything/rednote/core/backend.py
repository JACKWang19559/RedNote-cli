"""HTTP adapter for xpzouying/xiaohongshu-mcp.

Contract verified against upstream routes.go, handlers_api.go, types.go and
xiaohongshu/search.go. Keep website automation and cookies in the backend.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import __version__
from .errors import RedNoteError

SORT_OPTIONS = ("综合", "最新", "最多点赞", "最多评论", "最多收藏")
NOTE_TYPES = ("不限", "视频", "图文")
PUBLISH_TIMES = ("不限", "一天内", "一周内", "半年内")
SEARCH_SCOPES = ("不限", "已看过", "未看过", "已关注")
LOCATIONS = ("不限", "同城", "附近")
PROFILE_TABS = ("note", "fav", "liked")
NOTIFICATION_TABS = ("mentions", "likes", "connections")
SCROLL_SPEEDS = ("slow", "normal", "fast")


def _text(value: str, name: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RedNoteError("INVALID_ARGUMENT", f"{name} 不能为空。")
    if any(
        (ord(c) < 32 and c not in "\r\n\t")
        or ord(c) == 127
        or 0xD800 <= ord(c) <= 0xDFFF
        for c in value
    ):
        raise RedNoteError("INVALID_ARGUMENT", f"{name} 不能包含控制字符或无效 Unicode 字符。")
    if identifier:
        value = value.strip()
        if any(c.isspace() for c in value):
            raise RedNoteError("INVALID_ARGUMENT", f"{name} 不能包含空白字符。")
    return value


def _boolean(value: bool, name: str) -> bool:
    if not isinstance(value, bool):
        raise RedNoteError("INVALID_ARGUMENT", f"{name} 必须是布尔值。")
    return value


def _profile_tab(tab: str) -> str:
    aliases = {
        "": "note", "note": "note", "notes": "note", "笔记": "note",
        "fav": "fav", "favorites": "fav", "favorite": "fav", "收藏": "fav",
        "liked": "liked", "like": "liked", "点赞": "liked",
    }
    if not isinstance(tab, str) or tab.strip().lower() not in aliases:
        raise RedNoteError("INVALID_ARGUMENT", "主页 tab 可选值：note、fav、liked。")
    return aliases[tab.strip().lower()]


def _comment_config(config: dict[str, Any]) -> dict[str, Any]:
    allowed = {"click_more_replies", "max_replies_threshold", "max_comment_items", "scroll_speed"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise RedNoteError("INVALID_ARGUMENT", "comment_config 含不支持的字段或不是对象。")
    for name, value in config.items():
        if name == "click_more_replies":
            _boolean(value, name)
        elif name == "scroll_speed":
            if value not in SCROLL_SPEEDS:
                raise RedNoteError("INVALID_ARGUMENT", "scroll_speed 可选值：slow、normal、fast。")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RedNoteError("INVALID_ARGUMENT", f"{name} 必须是非负整数；0 使用后端默认值。")
    return dict(config)


def _outcome_unknown() -> RedNoteError:
    return RedNoteError(
        "BACKEND_OUTCOME_UNKNOWN", "无法确认写入结果，请求可能已经生效。",
        "请求未自动重试。请先在小红书或通过读取命令核对结果，再决定是否重试，避免重复发布或评论。",
    )


class Backend:
    """A bounded, non-retrying client for the backend's documented REST API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18060",
        timeout: float = 60.0,
        token: str | None = None,
    ) -> None:
        try:
            if not isinstance(base_url, str) or any(
                c.isspace() or ord(c) < 32 or ord(c) == 127 for c in base_url
            ):
                raise ValueError
            parsed = urlsplit(base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.port == 0
            ):
                raise ValueError
            # urlsplit accepts some hostnames/characters that HTTPX cannot
            # encode. Validate with the actual transport before any request.
            httpx.URL(base_url)
        except (TypeError, ValueError, httpx.InvalidURL):
            raise RedNoteError(
                "INVALID_BACKEND_URL",
                "后端地址必须是 HTTP(S) URL，且不能包含用户名、密码、查询参数或片段。",
            ) from None
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise RedNoteError("INVALID_TIMEOUT", "请求超时必须是大于零的有限秒数。")
        if token is not None and (not isinstance(token, str) or any(ord(c) < 32 or ord(c) > 126 for c in token)):
            raise RedNoteError("INVALID_BACKEND_TOKEN", "后端令牌必须是不包含控制字符的 ASCII 字符串。")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        headers = {"Accept": "application/json", "User-Agent": f"cli-anything-rednote/{__version__}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            headers=headers,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self) -> Backend:  # noqa: PYI034 -- Python 3.10 has no typing.Self.
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        mutation: bool = False,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(method, self.base_url + path, json=payload, params=params)
        except KeyboardInterrupt:
            if mutation:
                # Ctrl-C stops waiting, but cannot retract a request that the
                # backend may already have received and executed.
                raise _outcome_unknown() from None
            raise
        except httpx.InvalidURL:
            raise RedNoteError("INVALID_BACKEND_URL", "后端地址无法构成有效的 HTTP(S) 请求 URL。") from None
        except httpx.TimeoutException:
            if mutation:
                raise _outcome_unknown() from None
            raise RedNoteError(
                "BACKEND_TIMEOUT", "小红书后端请求超时。",
                "浏览器加载可能较慢；检查后端日志，或增大 --timeout 后重试。",
            ) from None
        except httpx.RequestError:
            if mutation:
                raise _outcome_unknown() from None
            raise RedNoteError(
                "BACKEND_UNAVAILABLE", "无法连接小红书后端。",
                "启动 xiaohongshu-mcp，检查后端地址，然后运行 rednote status。",
            ) from None

        # Never echo upstream details: browser errors can contain credentials,
        # xsec tokens, local filesystem paths or complete request bodies.
        if response.status_code in (401, 403):
            raise RedNoteError(
                "BACKEND_UNAUTHORIZED", "小红书后端拒绝了请求。",
                "检查 REDNOTE_BACKEND_TOKEN 是否与后端 Bearer Token 一致。",
            )
        if response.status_code == 429:
            raise RedNoteError("BACKEND_RATE_LIMITED", "后端请求过于频繁。", "请稍后重试。")
        if mutation and (response.status_code >= 500 or response.status_code == 408):
            raise _outcome_unknown()
        if not 200 <= response.status_code < 300:
            raise RedNoteError(
                "BACKEND_HTTP_ERROR", f"小红书后端返回 HTTP {response.status_code}。",
                "检查后端日志、登录状态和 API 版本；请求未自动重试。",
            )
        try:
            envelope = response.json()
        except (ValueError, UnicodeError):
            if mutation:
                raise _outcome_unknown() from None
            raise RedNoteError("INVALID_BACKEND_RESPONSE", "后端未返回有效 JSON。", "检查后端地址是否指向 xiaohongshu-mcp 服务根地址。") from None
        if not isinstance(envelope, dict):
            if mutation:
                raise _outcome_unknown()
            raise RedNoteError("INVALID_BACKEND_RESPONSE", "后端返回的 JSON 结构不符合预期。")
        if envelope.get("success") is False or "error" in envelope:
            hint = "请求未自动重试；请先核对实际结果和后端日志，避免重复操作。" if mutation else "检查后端日志及登录状态。"
            raise RedNoteError("BACKEND_ERROR", "小红书后端报告操作失败。", hint)
        if envelope.get("success") is not True or not isinstance(envelope.get("data"), dict):
            if mutation:
                raise _outcome_unknown()
            raise RedNoteError("INVALID_BACKEND_RESPONSE", "后端响应缺少成功标志或有效数据对象。")
        if mutation:
            result = envelope["data"]
            # Notification routes add another data wrapper. Inspect only that
            # wrapper chain, not user-authored content or arbitrary subtrees.
            while isinstance(result, dict):
                if result.get("success") is False or "error" in result:
                    raise RedNoteError("BACKEND_ERROR", "小红书后端报告写入失败。", "请核对实际结果；请求未自动重试。")
                result = result.get("data")
        return envelope["data"]

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def login_status(self) -> dict[str, Any]:
        data = self._request("GET", "/api/v1/login/status")
        self._validate_login(data)
        return data

    def login_qrcode(self) -> dict[str, Any]:
        data = self._request("GET", "/api/v1/login/qrcode")
        self._validate_login(data)
        if not data["is_logged_in"] and not isinstance(data.get("img"), str):
            raise RedNoteError("INVALID_BACKEND_RESPONSE", "后端响应缺少登录二维码。")
        return data

    @staticmethod
    def _validate_login(data: dict[str, Any]) -> None:
        if not isinstance(data.get("is_logged_in"), bool):
            raise RedNoteError("INVALID_BACKEND_RESPONSE", "后端响应缺少有效登录状态。")

    def feeds(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/feeds/list")

    def search(
        self,
        keyword: str,
        sort_by: str = "综合",
        note_type: str = "不限",
        publish_time: str = "不限",
        search_scope: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(keyword, str) or not keyword.strip():
            raise RedNoteError("INVALID_KEYWORD", "搜索关键词不能为空。")
        for name, value, allowed in (
            ("sort_by", sort_by, SORT_OPTIONS),
            ("note_type", note_type, NOTE_TYPES),
            ("publish_time", publish_time, PUBLISH_TIMES),
        ):
            if value not in allowed:
                raise RedNoteError("INVALID_FILTER", f"{name} 可选值：{'、'.join(allowed)}。")
        filters = {"sort_by": sort_by, "note_type": note_type, "publish_time": publish_time}
        for name, value, allowed in (("search_scope", search_scope, SEARCH_SCOPES), ("location", location, LOCATIONS)):
            if value is not None:
                if value not in allowed:
                    raise RedNoteError("INVALID_FILTER", f"{name} 可选值：{'、'.join(allowed)}。")
                filters[name] = value
        return self._request("POST", "/api/v1/feeds/search", {
            "keyword": keyword.strip(),
            "filters": filters,
        })

    def note(
        self, feed_id: str, xsec_token: str, load_all_comments: bool = False,
        *, comment_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .refs import parse_reference

        feed_id, xsec_token = parse_reference(feed_id, kind="note", xsec_token=xsec_token)
        if not isinstance(load_all_comments, bool):
            raise RedNoteError("INVALID_ARGUMENT", "load_all_comments 必须是布尔值。")
        payload = {
            "feed_id": feed_id, "xsec_token": xsec_token, "load_all_comments": load_all_comments,
        }
        if comment_config is not None:
            payload["comment_config"] = _comment_config(comment_config)
        return self._request("POST", "/api/v1/feeds/detail", payload)

    def profile(self, user_id: str, xsec_token: str, tab: str | None = None) -> dict[str, Any]:
        from .refs import parse_reference

        user_id, xsec_token = parse_reference(user_id, kind="user", xsec_token=xsec_token)
        payload = {"user_id": user_id, "xsec_token": xsec_token}
        if tab is not None:
            payload["tab"] = _profile_tab(tab)
        return self._request("POST", "/api/v1/user/profile", payload)

    def me(self, tab: str | None = None) -> dict[str, Any]:
        params = {"tab": _profile_tab(tab)} if tab is not None else None
        return self._request("GET", "/api/v1/user/me", params=params)

    def delete_cookies(self) -> dict[str, Any]:
        return self._request("DELETE", "/api/v1/login/cookies", mutation=True)

    def publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .publishing import validate_publish_payload

        payload = validate_publish_payload(payload, kind="image")
        return self._request("POST", "/api/v1/publish", payload, mutation=True)

    def publish_video(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .publishing import validate_publish_payload

        payload = validate_publish_payload(payload, kind="video")
        return self._request("POST", "/api/v1/publish_video", payload, mutation=True)

    @staticmethod
    def _note_target(feed_id: str, xsec_token: str) -> dict[str, str]:
        from .refs import parse_reference

        feed_id, xsec_token = parse_reference(feed_id, kind="note", xsec_token=xsec_token)
        return {"feed_id": feed_id, "xsec_token": xsec_token}

    def comment(self, feed_id: str, xsec_token: str, content: str) -> dict[str, Any]:
        payload = {**self._note_target(feed_id, xsec_token), "content": _text(content, "content")}
        return self._request("POST", "/api/v1/feeds/comment", payload, mutation=True)

    def reply_comment(
        self, feed_id: str, xsec_token: str, content: str,
        comment_id: str | None = None, user_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {**self._note_target(feed_id, xsec_token), "content": _text(content, "content")}
        if comment_id is None and user_id is None:
            raise RedNoteError("INVALID_ARGUMENT", "回复评论至少需要 comment_id 或 user_id。")
        if comment_id is not None:
            payload["comment_id"] = _text(comment_id, "comment_id", identifier=True)
        if user_id is not None:
            payload["user_id"] = _text(user_id, "user_id", identifier=True)
        return self._request("POST", "/api/v1/feeds/comment/reply", payload, mutation=True)

    def like(self, feed_id: str, xsec_token: str, unlike: bool = False) -> dict[str, Any]:
        payload = {**self._note_target(feed_id, xsec_token), "unlike": _boolean(unlike, "unlike")}
        return self._request("POST", "/api/v1/feeds/like", payload, mutation=True)

    def favorite(self, feed_id: str, xsec_token: str, unfavorite: bool = False) -> dict[str, Any]:
        payload = {**self._note_target(feed_id, xsec_token), "unfavorite": _boolean(unfavorite, "unfavorite")}
        return self._request("POST", "/api/v1/feeds/favorite", payload, mutation=True)

    def notifications_unread(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/notifications/unread")

    def notifications(self, tab: str = "mentions", limit: int = 20) -> dict[str, Any]:
        if not isinstance(tab, str) or tab.strip().lower() not in ("", *NOTIFICATION_TABS):
            raise RedNoteError("INVALID_ARGUMENT", "通知 tab 可选值：mentions、likes、connections。")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise RedNoteError("INVALID_ARGUMENT", "通知 limit 必须是非负整数；0 使用后端默认值。")
        return self._request("GET", "/api/v1/notifications/list", params={
            "tab": tab.strip().lower() or "mentions", "limit": limit,
        })

    def notification_reply(self, comment_id: str, content: str) -> dict[str, Any]:
        payload = {"comment_id": _text(comment_id, "comment_id", identifier=True), "content": _text(content, "content")}
        return self._request("POST", "/api/v1/notifications/reply", payload, mutation=True)

    def notification_like(self, comment_id: str, unlike: bool = False) -> dict[str, Any]:
        payload = {"comment_id": _text(comment_id, "comment_id", identifier=True), "unlike": _boolean(unlike, "unlike")}
        return self._request("POST", "/api/v1/notifications/like", payload, mutation=True)
