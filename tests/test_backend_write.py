"""Verified upstream write contracts, exercised only with an in-memory HTTP transport."""

import json

import httpx
import pytest

from cli_anything.rednote import __version__
from cli_anything.rednote.core.backend import Backend
from cli_anything.rednote.core.errors import RedNoteError

NOTE = "680000000000000000000001"
USER = "580000000000000000000002"
COMMENT = "680000000000000000000003"
TOKEN = "reference+token=="
IMAGE_PAYLOAD = {"title": "测试图片", "content": "正文", "images": ["https://example.org/photo.png"]}
VIDEO_PAYLOAD = {"title": "测试视频", "content": "正文", "video": "/backend/clip.mp4"}


@pytest.fixture
def make_backend():
    clients = []

    def make(handler):
        backend = Backend(token="backend-test-token")
        headers = backend._client.headers
        backend.close()
        backend._client = httpx.Client(transport=httpx.MockTransport(handler), headers=headers)
        clients.append(backend)
        return backend

    yield make
    for backend in clients:
        backend.close()


WRITE_CASES = [
    (lambda b: b.delete_cookies(), "DELETE", "/api/v1/login/cookies", None),
    (lambda b: b.publish(IMAGE_PAYLOAD), "POST", "/api/v1/publish", IMAGE_PAYLOAD),
    (lambda b: b.publish_video(VIDEO_PAYLOAD), "POST", "/api/v1/publish_video", VIDEO_PAYLOAD),
    (lambda b: b.comment(NOTE, TOKEN, " 保留正文\n换行 "), "POST", "/api/v1/feeds/comment", {"feed_id": NOTE, "xsec_token": TOKEN, "content": " 保留正文\n换行 "}),
    (lambda b: b.reply_comment(NOTE, TOKEN, "回复", comment_id=COMMENT), "POST", "/api/v1/feeds/comment/reply", {"feed_id": NOTE, "xsec_token": TOKEN, "content": "回复", "comment_id": COMMENT}),
    (lambda b: b.reply_comment(NOTE, TOKEN, "回复", user_id=USER), "POST", "/api/v1/feeds/comment/reply", {"feed_id": NOTE, "xsec_token": TOKEN, "content": "回复", "user_id": USER}),
    (lambda b: b.reply_comment(NOTE, TOKEN, "回复", comment_id=COMMENT, user_id=USER), "POST", "/api/v1/feeds/comment/reply", {"feed_id": NOTE, "xsec_token": TOKEN, "content": "回复", "comment_id": COMMENT, "user_id": USER}),
    (lambda b: b.like(NOTE, TOKEN), "POST", "/api/v1/feeds/like", {"feed_id": NOTE, "xsec_token": TOKEN, "unlike": False}),
    (lambda b: b.like(NOTE, TOKEN, unlike=True), "POST", "/api/v1/feeds/like", {"feed_id": NOTE, "xsec_token": TOKEN, "unlike": True}),
    (lambda b: b.favorite(NOTE, TOKEN), "POST", "/api/v1/feeds/favorite", {"feed_id": NOTE, "xsec_token": TOKEN, "unfavorite": False}),
    (lambda b: b.favorite(NOTE, TOKEN, unfavorite=True), "POST", "/api/v1/feeds/favorite", {"feed_id": NOTE, "xsec_token": TOKEN, "unfavorite": True}),
    (lambda b: b.notification_reply(COMMENT, "感谢"), "POST", "/api/v1/notifications/reply", {"comment_id": COMMENT, "content": "感谢"}),
    (lambda b: b.notification_like(COMMENT), "POST", "/api/v1/notifications/like", {"comment_id": COMMENT, "unlike": False}),
    (lambda b: b.notification_like(COMMENT, unlike=True), "POST", "/api/v1/notifications/like", {"comment_id": COMMENT, "unlike": True}),
]


@pytest.mark.parametrize("operation,method,path,payload", WRITE_CASES)
def test_exact_write_rest_contract(make_backend, operation, method, path, payload):
    requests = []
    data = {"data": {"comment_id": COMMENT, "liked": True, "skipped": False}} if "/notifications/" in path else {"success": True}

    def handler(request):
        requests.append(request)
        assert request.method == method
        assert request.url.path == path
        assert not request.url.query
        assert request.headers["Authorization"] == "Bearer backend-test-token"
        assert request.headers["User-Agent"] == f"cli-anything-rednote/{__version__}"
        assert (json.loads(request.content) if request.content else None) == payload
        return httpx.Response(200, json={"success": True, "data": data})

    assert operation(make_backend(handler)) == data
    assert len(requests) == 1


@pytest.mark.parametrize("operation,method,path,payload", WRITE_CASES)
def test_every_write_marks_timeout_as_unknown_and_does_not_retry(make_backend, operation, method, path, payload):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private request token", request=request)

    with pytest.raises(RedNoteError) as caught:
        operation(make_backend(handler))
    assert caught.value.code == "BACKEND_OUTCOME_UNKNOWN"
    assert "可能已经生效" in caught.value.message
    assert "未自动重试" in caught.value.hint
    assert "private" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.WriteError, httpx.RemoteProtocolError])
def test_mutation_transport_failures_are_unknown(make_backend, error_type):
    calls = []

    def handler(request):
        calls.append(request)
        raise error_type("private token", request=request)

    with pytest.raises(RedNoteError) as caught:
        make_backend(handler).comment(NOTE, TOKEN, "正文")
    assert caught.value.code == "BACKEND_OUTCOME_UNKNOWN"
    assert len(calls) == 1


def test_interrupted_write_has_unknown_outcome_and_is_not_retried(make_backend):
    calls = []

    def handler(request):
        calls.append(request)
        raise KeyboardInterrupt

    with pytest.raises(RedNoteError) as caught:
        make_backend(handler).comment(NOTE, TOKEN, "可能已经发送的评论")
    assert caught.value.code == "BACKEND_OUTCOME_UNKNOWN"
    assert "可能已经生效" in caught.value.message
    assert "未自动重试" in caught.value.hint
    assert len(calls) == 1
    assert calls[0].url.path == "/api/v1/feeds/comment"


def test_interrupted_read_preserves_keyboard_interrupt(make_backend):
    calls = []

    def handler(request):
        calls.append(request)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        make_backend(handler).note(NOTE, TOKEN)
    assert len(calls) == 1


@pytest.mark.parametrize("codepoint", [0xD800, 0xDFFF])
def test_comment_surrogates_fail_validation_before_encoding_or_network(make_backend, codepoint):
    def handler(_):
        pytest.fail("Invalid Unicode reached the backend")

    backend = make_backend(handler)
    surrogate = chr(codepoint)
    for action in (
        lambda: backend.comment(NOTE, TOKEN, "正文" + surrogate),
        lambda: backend.notification_reply(COMMENT, surrogate),
        lambda: backend.reply_comment(NOTE, TOKEN, "回复", comment_id=surrogate),
    ):
        with pytest.raises(RedNoteError) as caught:
            action()
        assert caught.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": "verification failed after submit"}),
    httpx.Response(502, text="gateway failure"),
    httpx.Response(408, text="request timeout"),
    httpx.Response(200, text="not JSON"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"success": True, "data": None}),
])
def test_unconfirmed_mutation_responses_are_unknown(make_backend, response):
    with pytest.raises(RedNoteError) as caught:
        make_backend(lambda _: response).comment(NOTE, TOKEN, "正文")
    assert caught.value.code == "BACKEND_OUTCOME_UNKNOWN"


@pytest.mark.parametrize("data", [
    {"success": False, "message": "private token"},
    {"data": {"success": False}},
    {"data": {"data": {"success": False}}},
    {"data": {"error": "private token"}},
])
def test_nested_mutation_failure_is_never_reported_as_success(make_backend, data):
    with pytest.raises(RedNoteError) as caught:
        make_backend(lambda _: httpx.Response(200, json={"success": True, "data": data})).notification_like(COMMENT)
    assert caught.value.code == "BACKEND_ERROR"
    assert "private token" not in str(caught.value)


@pytest.mark.parametrize("operation", [
    lambda b: b.comment(NOTE, TOKEN, " "),
    lambda b: b.comment(NOTE, TOKEN, "nul\x00"),
    lambda b: b.reply_comment(NOTE, TOKEN, "正文"),
    lambda b: b.reply_comment(NOTE, TOKEN, "正文", comment_id=""),
    lambda b: b.reply_comment(NOTE, TOKEN, "正文", user_id="bad id"),
    lambda b: b.like(NOTE, TOKEN, unlike="false"),
    lambda b: b.favorite(NOTE, TOKEN, unfavorite=0),
    lambda b: b.notification_reply(" ", "正文"),
    lambda b: b.notification_reply(COMMENT, "\x7f"),
    lambda b: b.notification_like(COMMENT, unlike=None),
    lambda b: b.notifications("unknown"),
    lambda b: b.notifications(limit=True),
    lambda b: b.notifications(limit=-1),
    lambda b: b.notifications(limit=1.5),
    lambda b: b.profile(USER, TOKEN, tab="unknown"),
    lambda b: b.me(tab=1),
])
def test_invalid_actions_fail_before_network(make_backend, operation):
    def handler(_):
        pytest.fail("Invalid action reached the backend")
    with pytest.raises(RedNoteError) as caught:
        operation(make_backend(handler))
    assert caught.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("operation,path,params", [
    (lambda b: b.notifications_unread(), "/api/v1/notifications/unread", {}),
    (lambda b: b.notifications(), "/api/v1/notifications/list", {"tab": "mentions", "limit": "20"}),
    (lambda b: b.notifications(" Likes ", 50), "/api/v1/notifications/list", {"tab": "likes", "limit": "50"}),
    (lambda b: b.notifications("connections", 0), "/api/v1/notifications/list", {"tab": "connections", "limit": "0"}),
    (lambda b: b.me(tab="收藏"), "/api/v1/user/me", {"tab": "fav"}),
])
def test_read_query_contract(make_backend, operation, path, params):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == path
        assert dict(request.url.params) == params
        assert not request.content
        return httpx.Response(200, json={"success": True, "data": {"data": {"items": []}}})
    assert operation(make_backend(handler)) == {"data": {"items": []}}


def test_additional_search_filters_are_nested_and_do_not_change_defaults(make_backend):
    def handler(request):
        assert json.loads(request.content) == {
            "keyword": "咖啡", "filters": {
                "sort_by": "综合", "note_type": "不限", "publish_time": "不限",
                "search_scope": "已关注", "location": "同城",
            },
        }
        return httpx.Response(200, json={"success": True, "data": {"feeds": [], "count": 0}})
    make_backend(handler).search("咖啡", search_scope="已关注", location="同城")


@pytest.mark.parametrize("kwargs", [{"search_scope": "bad"}, {"location": "bad"}])
def test_additional_filters_are_validated(make_backend, kwargs):
    def handler(_):
        pytest.fail("Invalid filter reached the backend")
    with pytest.raises(RedNoteError) as caught:
        make_backend(handler).search("咖啡", **kwargs)
    assert caught.value.code == "INVALID_FILTER"


def test_note_comment_controls_use_nested_payload(make_backend):
    config = {"click_more_replies": True, "max_replies_threshold": 0, "max_comment_items": 100, "scroll_speed": "slow"}

    def handler(request):
        assert json.loads(request.content) == {"feed_id": NOTE, "xsec_token": TOKEN, "load_all_comments": True, "comment_config": config}
        return httpx.Response(200, json={"success": True, "data": {"feed_id": NOTE, "data": {}}})
    make_backend(handler).note(NOTE, TOKEN, True, comment_config=config)


@pytest.mark.parametrize("config", [
    [], {"unknown": 1}, {"max_comment_items": -1}, {"max_comment_items": True},
    {"max_replies_threshold": 1.5}, {"click_more_replies": 1}, {"scroll_speed": "turbo"},
])
def test_invalid_comment_controls_fail_before_network(make_backend, config):
    def handler(_):
        pytest.fail("Invalid config reached the backend")
    with pytest.raises(RedNoteError) as caught:
        make_backend(handler).note(NOTE, TOKEN, comment_config=config)
    assert caught.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("tab,canonical", [("note", "note"), (" NOTES ", "note"), ("收藏", "fav"), ("favorites", "fav"), ("点赞", "liked")])
def test_profile_tab_aliases_match_upstream(make_backend, tab, canonical):
    def handler(request):
        assert json.loads(request.content) == {"user_id": USER, "xsec_token": TOKEN, "tab": canonical}
        return httpx.Response(200, json={"success": True, "data": {"data": {}}})
    make_backend(handler).profile(USER, TOKEN, tab=tab)


def test_read_only_post_timeout_does_not_claim_write_outcome(make_backend):
    def handler(request):
        raise httpx.ReadTimeout("slow read", request=request)
    with pytest.raises(RedNoteError) as caught:
        make_backend(handler).note(NOTE, TOKEN)
    assert caught.value.code == "BACKEND_TIMEOUT"


@pytest.mark.parametrize("method,payload", [("publish", {"title": "only title"}), ("publish_video", {"title": "only title"})])
def test_publish_validation_precedes_network(make_backend, method, payload):
    def handler(_):
        pytest.fail("Invalid publish payload reached the backend")
    with pytest.raises(RedNoteError):
        getattr(make_backend(handler), method)(payload)
