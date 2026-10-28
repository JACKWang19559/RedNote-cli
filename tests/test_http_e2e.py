"""Installed CLI over controlled loopback HTTP, not live Xiaohongshu validation.

These tests use real subprocesses and HTTP sockets against a local contract
fixture. No browser, remote backend, or platform account is involved.
"""

import json
import os
import shutil
import subprocess
import sysconfig
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import quote

import pytest

NOTE_ID = "680000000000000000000001"
USER_ID = "580000000000000000000002"
BACKEND_TOKEN = "loopback-backend-secret"
NOTE_TOKEN = "opaque%2Fsegment+/="


def success(data):
    return 200, {"success": True, "data": data, "message": "成功"}


@pytest.fixture
def loopback_backend(tmp_path):
    """Only bind an ephemeral loopback socket; record actual wire requests."""
    executable = shutil.which("rednote", path=sysconfig.get_path("scripts"))
    assert executable, "Install the package first: python -m pip install -e ."
    calls = []
    responses = {
        ("GET", "/health"): success({"status": "healthy"}),
        ("GET", "/api/v1/login/status"): success(
            {"is_logged_in": True, "username": "测试用户"}
        ),
        ("GET", "/api/v1/user/me"): success(
            {"data": {"userBasicInfo": {"nickname": "测试用户"}, "feeds": []}}
        ),
        ("POST", "/api/v1/feeds/search"): success(
            {
                "feeds": [
                    {
                        "id": NOTE_ID,
                        "xsecToken": NOTE_TOKEN,
                        "noteCard": {"displayTitle": "咖啡地图"},
                    }
                ],
                "count": 1,
            }
        ),
        ("POST", "/api/v1/feeds/detail"): success(
            {
                "feed_id": NOTE_ID,
                "data": {
                    "note": {"title": "咖啡地图", "desc": "中文正文 ☕"},
                    "comments": {"list": [], "hasMore": False},
                },
            }
        ),
        ("POST", "/api/v1/user/profile"): success(
            {"data": {"userBasicInfo": {"nickname": "作者"}, "feeds": []}}
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": json.loads(raw) if raw else None,
                }
            )
            status, body = responses.get(
                (self.command, self.path), (404, {"error": "unexpected fixture route"})
            )
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    unrelated = tmp_path / "unrelated working directory"
    unrelated.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "REDNOTE_HOME": str(tmp_path / "isolated data"),
            "REDNOTE_BACKEND_URL": f"http://127.0.0.1:{server.server_port}",
            "REDNOTE_BACKEND_TOKEN": BACKEND_TOKEN,
            "REDNOTE_TIMEOUT": "3",
            "PYTHONIOENCODING": "utf-8",
            # This local client should not inherit ambient proxy settings.
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )

    def run(*args, expected=0):
        process = subprocess.run(
            [executable, "--json", *args],
            cwd=unrelated,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        assert process.returncode == expected, process.stdout + process.stderr
        assert process.stderr == ""
        document = json.loads(process.stdout)
        assert document["ok"] is (expected == 0)
        return document, process.stdout + process.stderr

    try:
        yield SimpleNamespace(run=run, calls=calls, responses=responses)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_installed_search_note_and_author_workflow_over_loopback(loopback_backend):
    backend = loopback_backend
    search, _ = backend.run(
        "search", " 上海咖啡 ", "--sort", "最新", "--type", "图文", "--time", "一周内"
    )
    found = search["data"]["feeds"][0]
    note_url = (
        f"https://www.xiaohongshu.com/explore/{found['id']}"
        f"?xsec_token={quote(found['xsecToken'], safe='')}&xsec_source=pc_search"
    )
    note, _ = backend.run("note", "get", note_url, "--all-comments")
    assert note["data"]["data"]["note"]["desc"] == "中文正文 ☕"
    profile_url = (
        f"https://www.xiaohongshu.com/user/profile/{USER_ID}"
        "?xsec_token=profile%2Btoken%3D"
    )
    profile, _ = backend.run("user", "get", profile_url)
    assert profile["data"]["data"]["userBasicInfo"]["nickname"] == "作者"

    assert [(call["method"], call["path"]) for call in backend.calls] == [
        ("POST", "/api/v1/feeds/search"),
        ("POST", "/api/v1/feeds/detail"),
        ("POST", "/api/v1/user/profile"),
    ]
    assert [call["body"] for call in backend.calls] == [
        {
            "keyword": "上海咖啡",
            "filters": {
                "sort_by": "最新",
                "note_type": "图文",
                "publish_time": "一周内",
            },
        },
        {
            "feed_id": NOTE_ID,
            "xsec_token": NOTE_TOKEN,
            "load_all_comments": True,
        },
        {"user_id": USER_ID, "xsec_token": "profile+token="},
    ]
    assert all(
        call["authorization"] == f"Bearer {BACKEND_TOKEN}" for call in backend.calls
    )
    assert all(call["content_type"] == "application/json" for call in backend.calls)


def test_installed_me_status_and_auth_over_loopback(loopback_backend):
    backend = loopback_backend
    me, _ = backend.run("user", "me")
    assert me["data"]["data"]["userBasicInfo"]["nickname"] == "测试用户"
    status, _ = backend.run("status")
    assert status["data"] == {
        "health": {"status": "healthy"},
        "login": {"is_logged_in": True, "username": "测试用户"},
    }
    auth, _ = backend.run("auth", "status")
    assert auth["data"]["is_logged_in"] is True
    assert [(call["method"], call["path"]) for call in backend.calls] == [
        ("GET", "/api/v1/user/me"),
        ("GET", "/health"),
        ("GET", "/api/v1/login/status"),
        ("GET", "/api/v1/login/status"),
    ]
    assert all(call["body"] is None for call in backend.calls)
    assert all(
        call["authorization"] == f"Bearer {BACKEND_TOKEN}" for call in backend.calls
    )


@pytest.mark.parametrize(
    "status,error_code", [(401, "BACKEND_UNAUTHORIZED"), (200, "BACKEND_ERROR")]
)
def test_installed_http_failures_are_redacted_and_not_retried(
    loopback_backend, status, error_code
):
    backend = loopback_backend
    backend.responses[("POST", "/api/v1/feeds/search")] = (
        status,
        {
            "success": False,
            "error": "UPSTREAM_ERROR",
            "message": f"sensitive detail: {BACKEND_TOKEN}",
            "details": "private-cookie-value C:/private/browser/profile",
        },
    )
    failure, output = backend.run("search", "咖啡", expected=1)
    assert failure["error"]["code"] == error_code
    for secret in (BACKEND_TOKEN, "private-cookie-value", "C:/private", "Traceback"):
        assert secret not in output
    assert len(backend.calls) == 1
    assert backend.calls[0]["path"] == "/api/v1/feeds/search"
