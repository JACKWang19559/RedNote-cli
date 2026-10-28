"""Contract tests based on upstream REST routes, not a live account."""

import json

import httpx
import pytest

from cli_anything.rednote.core.backend import Backend
from cli_anything.rednote.core.errors import RedNoteError

NOTE_ID = "680000000000000000000001"
USER_ID = "580000000000000000000002"


@pytest.fixture
def backend_factory():
    clients = []

    def make(handler, **kwargs):
        backend = Backend(**kwargs)
        headers = backend._client.headers
        backend._client.close()
        backend._client = httpx.Client(transport=httpx.MockTransport(handler), headers=headers)
        clients.append(backend)
        return backend

    yield make
    for client in clients:
        client.close()


@pytest.mark.parametrize("operation,method,path,payload,data", [
    (lambda b: b.health(), "GET", "/health", None, {"status": "healthy"}),
    (lambda b: b.login_status(), "GET", "/api/v1/login/status", None, {"is_logged_in": False}),
    (lambda b: b.login_qrcode(), "GET", "/api/v1/login/qrcode", None, {"is_logged_in": False, "img": "data:image/png;base64,YQ==", "timeout": "4m0s"}),
    (lambda b: b.feeds(), "GET", "/api/v1/feeds/list", None, {"feeds": [], "count": 0}),
    (lambda b: b.search(" 咖啡 ", "最新", "图文", "一周内"), "POST", "/api/v1/feeds/search", {"keyword": "咖啡", "filters": {"sort_by": "最新", "note_type": "图文", "publish_time": "一周内"}}, {"feeds": [], "count": 0}),
    (lambda b: b.note(NOTE_ID, "abc+/%=", True), "POST", "/api/v1/feeds/detail", {"feed_id": NOTE_ID, "xsec_token": "abc+/%=", "load_all_comments": True}, {"feed_id": NOTE_ID, "data": {"note": {"title": "咖啡"}}}),
    (lambda b: b.profile(USER_ID, "token"), "POST", "/api/v1/user/profile", {"user_id": USER_ID, "xsec_token": "token"}, {"data": {"userBasicInfo": {"nickname": "测试"}, "feeds": []}}),
    (lambda b: b.me(), "GET", "/api/v1/user/me", None, {"data": {"userBasicInfo": {"nickname": "测试"}}}),
])
def test_upstream_rest_contract(backend_factory, operation, method, path, payload, data):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == method
        assert request.url.path == path
        assert request.headers["Authorization"] == "Bearer backend-secret"
        if payload is None:
            assert request.content == b""
        else:
            assert json.loads(request.content) == payload
        return httpx.Response(200, json={"success": True, "data": data, "message": "成功"})

    backend = backend_factory(handler, token="backend-secret")
    assert operation(backend) == data
    assert len(calls) == 1


@pytest.mark.parametrize("status,code", [
    (401, "BACKEND_UNAUTHORIZED"), (403, "BACKEND_UNAUTHORIZED"),
    (429, "BACKEND_RATE_LIMITED"), (500, "BACKEND_HTTP_ERROR"),
    (404, "BACKEND_HTTP_ERROR"), (302, "BACKEND_HTTP_ERROR"),
])
def test_http_errors_do_not_leak_response_or_retry(backend_factory, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "secret-token and local-path", "details": "private-data"}, headers={"Location": "https://example.org"})

    with pytest.raises(RedNoteError) as exc:
        backend_factory(handler).search("咖啡")
    assert exc.value.code == code
    assert "secret-token" not in str(exc.value)
    assert "private-data" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("body,code", [
    ({"success": False, "data": {}}, "BACKEND_ERROR"),
    ({"error": "secret"}, "BACKEND_ERROR"),
    ({"success": True}, "INVALID_BACKEND_RESPONSE"),
    ({"success": 1, "data": {}}, "INVALID_BACKEND_RESPONSE"),
    ({"success": "true", "data": {}}, "INVALID_BACKEND_RESPONSE"),
    ({"success": True, "data": None}, "INVALID_BACKEND_RESPONSE"),
    ({"success": True, "data": []}, "INVALID_BACKEND_RESPONSE"),
    ([], "INVALID_BACKEND_RESPONSE"),
])
def test_invalid_json_shapes(backend_factory, body, code):
    with pytest.raises(RedNoteError) as exc:
        backend_factory(lambda _: httpx.Response(200, json=body)).health()
    assert exc.value.code == code


def test_non_json_response(backend_factory):
    with pytest.raises(RedNoteError) as exc:
        backend_factory(lambda _: httpx.Response(200, text="<html>proxy failure</html>")).health()
    assert exc.value.code == "INVALID_BACKEND_RESPONSE"


@pytest.mark.parametrize("exception,code", [(httpx.ConnectError, "BACKEND_UNAVAILABLE"), (httpx.ReadTimeout, "BACKEND_TIMEOUT")])
def test_transport_error_redaction(backend_factory, exception, code):
    def handler(request):
        raise exception("private URL and token", request=request)

    with pytest.raises(RedNoteError) as exc:
        backend_factory(handler).health()
    assert exc.value.code == code
    assert "private URL" not in str(exc.value)
    if code == "BACKEND_UNAVAILABLE":
        assert "rednote status" in exc.value.hint


@pytest.mark.parametrize("url", ["ftp://localhost", "localhost:18060", "http://u:p@localhost", "http://localhost/?token=secret", "http://localhost/#test", "http://localhost:bad", "http://localhost:0", "http://local host", "http://[bad", "http://\u2603.example", "http://example.com/\x00", "http://example.com/\x7f", "\x00http://localhost", None, b"http://localhost"])
def test_invalid_backend_urls(url):
    with pytest.raises(RedNoteError) as exc:
        Backend(url)
    assert exc.value.code == "INVALID_BACKEND_URL"


def test_transport_invalid_url_is_redacted(backend_factory):
    def handler(_):
        raise httpx.InvalidURL("private URL and token")

    with pytest.raises(RedNoteError) as exc:
        backend_factory(handler).health()
    assert exc.value.code == "INVALID_BACKEND_URL"
    assert "private URL" not in str(exc.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "60"])
def test_invalid_timeout(timeout):
    with pytest.raises(RedNoteError) as exc:
        Backend(timeout=timeout)
    assert exc.value.code == "INVALID_TIMEOUT"


@pytest.mark.parametrize("token", ["secret\nvalue", "中文", "secret\x00"])
def test_invalid_auth_token(token):
    with pytest.raises(RedNoteError) as exc:
        Backend(token=token)
    assert exc.value.code == "INVALID_BACKEND_TOKEN"


def test_reverse_proxy_prefix_preserved(backend_factory):
    def handler(request):
        assert request.url.path == "/rednote/health"
        return httpx.Response(200, json={"success": True, "data": {"status": "healthy"}})
    assert backend_factory(handler, base_url="https://backend.example/rednote/").health()["status"] == "healthy"


@pytest.mark.parametrize("kwargs", [{"keyword": " "}, {"keyword": "x", "sort_by": "bad"}, {"keyword": "x", "note_type": "bad"}, {"keyword": "x", "publish_time": "bad"}])
def test_search_validation_precedes_network(backend_factory, kwargs):
    def handler(_):
        pytest.fail("Invalid requests must not reach backend")
    with pytest.raises(RedNoteError):
        backend_factory(handler).search(**kwargs)


@pytest.mark.parametrize("method,data", [("login_status", {"is_logged_in": "false"}), ("login_qrcode", {"is_logged_in": False})])
def test_login_shape_validation(backend_factory, method, data):
    backend = backend_factory(lambda _: httpx.Response(200, json={"success": True, "data": data}))
    with pytest.raises(RedNoteError) as exc:
        getattr(backend, method)()
    assert exc.value.code == "INVALID_BACKEND_RESPONSE"


def test_context_manager_closes_client():
    with Backend() as backend:
        assert not backend._client.is_closed
    assert backend._client.is_closed
