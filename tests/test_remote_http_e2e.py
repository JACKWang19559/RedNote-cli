"""Installed v0.2 CLI workflows over controlled loopback HTTP only.

All remote effects are simulated by this local server. These tests never
connect to Xiaohongshu, upload media, or operate a real account.
"""

import json
import os
import shutil
import subprocess
import sysconfig
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest

NOTE = "680000000000000000000001"
USER = "580000000000000000000002"
COMMENT = "680000000000000000000003"
NOTE_TOKEN = "note%2Ftoken+/="
AUTH_TOKEN = "isolated-loopback-token"
NOTE_URL = f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token={quote(NOTE_TOKEN, safe='')}"


@pytest.fixture
def remote_loopback(tmp_path):
    executable = shutil.which("rednote", path=sysconfig.get_path("scripts"))
    assert executable, "Install first with python -m pip install -e ."
    calls = []
    responses = {}
    routes = {
        ("POST", "/api/v1/publish"),
        ("POST", "/api/v1/publish_video"),
        ("POST", "/api/v1/feeds/like"),
        ("POST", "/api/v1/feeds/favorite"),
        ("POST", "/api/v1/feeds/comment"),
        ("POST", "/api/v1/feeds/comment/reply"),
        ("POST", "/api/v1/feeds/search"),
        ("POST", "/api/v1/feeds/detail"),
        ("POST", "/api/v1/user/profile"),
        ("GET", "/api/v1/user/me"),
        ("GET", "/api/v1/notifications/unread"),
        ("GET", "/api/v1/notifications/list"),
        ("POST", "/api/v1/notifications/reply"),
        ("POST", "/api/v1/notifications/like"),
        ("DELETE", "/api/v1/login/cookies"),
    }

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            parsed = urlsplit(self.path)
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append({
                "method": self.command,
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "body": json.loads(raw) if raw else None,
                "authorization": self.headers.get("Authorization"),
                "user_agent": self.headers.get("User-Agent"),
            })
            key = (self.command, parsed.path)
            if key in responses:
                response = responses[key]
                if response is None:
                    # Close after reading the complete request. The client
                    # cannot know whether an upstream mutation took effect.
                    self.close_connection = True
                    return
                status, body = response
            elif key in routes:
                status, body = 200, {"success": True, "data": {"fixture": "accepted"}}
            else:
                status, body = 404, {"error": "Unexpected fixture route"}
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    cwd = tmp_path / "independent directory"
    cwd.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({
        "REDNOTE_HOME": str(tmp_path / "isolated drafts"),
        "REDNOTE_BACKEND_URL": f"http://127.0.0.1:{server.server_port}",
        "REDNOTE_BACKEND_TOKEN": AUTH_TOKEN,
        "REDNOTE_TIMEOUT": "2",
        "PYTHONIOENCODING": "utf-8",
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "http://127.0.0.1:1",
        "NO_PROXY": "",
    })

    def run(*args, expected=0):
        result = subprocess.run(
            [executable, "--json", *args], cwd=cwd, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=20, check=False,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        assert not result.stderr, result.stderr
        document = json.loads(result.stdout)
        assert document["ok"] is (expected == 0)
        return document

    try:
        yield SimpleNamespace(run=run, calls=calls, responses=responses, cwd=cwd, root=tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert all(call["authorization"] == f"Bearer {AUTH_TOKEN}" for call in calls)
        assert all(call["user_agent"] == "cli-anything-rednote/0.2.0" for call in calls)


def test_installed_image_publish_preserves_chinese_files_and_all_fields(remote_loopback):
    fixture = remote_loopback
    content_file = fixture.cwd / "中文正文.txt"
    content_file.write_text("周末咖啡 ☕\n第二行正文", encoding="utf-8-sig")
    image = fixture.cwd / "测试 图片.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture-only")
    schedule = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat()
    fixture.run(
        "publish", "image", "--title", "周末咖啡", "--content-file", str(content_file),
        "--image", str(image), "--image", "https://example.org/second.png",
        "--tag", "#咖啡", "--tag", "周末", "--product", "咖啡杯", "--original",
        "--schedule-at", schedule, "--visibility", "仅自己可见",
    )
    assert len(fixture.calls) == 1
    assert fixture.calls[0]["path"] == "/api/v1/publish"
    assert fixture.calls[0]["body"] == {
        "title": "周末咖啡", "content": "周末咖啡 ☕\n第二行正文",
        "images": [str(image.resolve()), "https://example.org/second.png"],
        "tags": ["咖啡", "周末"], "products": ["咖啡杯"], "is_original": True,
        "schedule_at": schedule, "visibility": "仅自己可见",
    }


def test_installed_video_uses_backend_host_path(remote_loopback):
    fixture = remote_loopback
    backend_path = "C:\\backend-only\\视频 文件.mp4"
    fixture.run(
        "publish", "video", "--title", "咖啡制作", "--content", "视频正文",
        "--video", backend_path, "--server-paths", "--tag", "咖啡",
        "--product", "咖啡豆", "--visibility", "仅互关好友可见",
    )
    assert len(fixture.calls) == 1
    assert fixture.calls[0]["path"] == "/api/v1/publish_video"
    assert fixture.calls[0]["body"] == {
        "title": "咖啡制作", "content": "视频正文", "video": backend_path,
        "tags": ["咖啡"], "products": ["咖啡豆"], "visibility": "仅互关好友可见",
    }


def test_installed_draft_publish_leaves_local_draft_unchanged(remote_loopback):
    fixture = remote_loopback
    before = fixture.run(
        "draft", "create", "--title", "待发布草稿", "--content", "草稿正文",
        "--image", "https://example.org/draft.png", "--tag", "咖啡",
    )["data"]
    fixture.run("draft", "publish", before["id"], "--visibility", "仅自己可见", "--original")
    after = fixture.run("draft", "show", before["id"])["data"]
    assert after == before
    assert len(fixture.calls) == 1
    assert fixture.calls[0]["path"] == "/api/v1/publish"
    assert fixture.calls[0]["body"] == {
        "title": before["title"], "content": before["content"], "images": before["images"],
        "tags": before["tags"], "products": [], "visibility": "仅自己可见", "is_original": True,
    }
    # Publication did not append an undo operation or replace the draft.
    undone = fixture.run("draft", "undo")["data"]
    assert undone["id"] == before["id"] and undone["draft"] is None
    assert len(fixture.calls) == 1


def test_installed_note_interactions_and_comment_workflow(remote_loopback):
    fixture = remote_loopback
    for action in ("like", "unlike", "favorite", "unfavorite"):
        fixture.run("note", action, NOTE_URL)
    fixture.run("comment", "add", NOTE, "--xsec-token", NOTE_TOKEN, "--content", "很好喝 ☕")
    fixture.run(
        "comment", "reply", NOTE_URL, "--comment-id", COMMENT, "--content", "谢谢分享",
    )
    fixture.run(
        "comment", "reply", NOTE_URL, "--user-id", USER, "--content", "回复作者",
    )
    target = {"feed_id": NOTE, "xsec_token": NOTE_TOKEN}
    assert [(call["path"], call["body"]) for call in fixture.calls] == [
        ("/api/v1/feeds/like", {**target, "unlike": False}),
        ("/api/v1/feeds/like", {**target, "unlike": True}),
        ("/api/v1/feeds/favorite", {**target, "unfavorite": False}),
        ("/api/v1/feeds/favorite", {**target, "unfavorite": True}),
        ("/api/v1/feeds/comment", {**target, "content": "很好喝 ☕"}),
        ("/api/v1/feeds/comment/reply", {**target, "comment_id": COMMENT, "content": "谢谢分享"}),
        ("/api/v1/feeds/comment/reply", {**target, "user_id": USER, "content": "回复作者"}),
    ]


def test_installed_notifications_and_logout_workflow(remote_loopback):
    fixture = remote_loopback
    fixture.run("notifications", "unread")
    fixture.run("notifications", "list", "--tab", "connections", "--limit", "35")
    fixture.run("notifications", "reply", COMMENT, "--content", "收到，谢谢")
    fixture.run("notifications", "like", COMMENT)
    fixture.run("notifications", "unlike", COMMENT)
    fixture.run("auth", "logout")
    assert [(call["method"], call["path"], call["query"], call["body"]) for call in fixture.calls] == [
        ("GET", "/api/v1/notifications/unread", {}, None),
        ("GET", "/api/v1/notifications/list", {"tab": ["connections"], "limit": ["35"]}, None),
        ("POST", "/api/v1/notifications/reply", {}, {"comment_id": COMMENT, "content": "收到，谢谢"}),
        ("POST", "/api/v1/notifications/like", {}, {"comment_id": COMMENT, "unlike": False}),
        ("POST", "/api/v1/notifications/like", {}, {"comment_id": COMMENT, "unlike": True}),
        ("DELETE", "/api/v1/login/cookies", {}, None),
    ]


def test_installed_extended_read_options_reach_backend(remote_loopback):
    fixture = remote_loopback
    fixture.run("search", "咖啡", "--scope", "已关注", "--location", "同城")
    fixture.run(
        "note", "get", NOTE_URL, "--comment-limit", "40", "--expand-replies",
        "--reply-limit", "15", "--scroll-speed", "slow",
    )
    fixture.run("user", "get", USER, "--xsec-token", "profile-token", "--tab", "fav")
    fixture.run("user", "me", "--tab", "liked")
    assert fixture.calls[0]["body"] == {
        "keyword": "咖啡", "filters": {"sort_by": "综合", "note_type": "不限", "publish_time": "不限", "search_scope": "已关注", "location": "同城"},
    }
    assert fixture.calls[1]["body"] == {
        "feed_id": NOTE, "xsec_token": NOTE_TOKEN, "load_all_comments": True,
        "comment_config": {"max_comment_items": 40, "click_more_replies": True, "max_replies_threshold": 15, "scroll_speed": "slow"},
    }
    assert fixture.calls[2]["body"] == {"user_id": USER, "xsec_token": "profile-token", "tab": "fav"}
    assert fixture.calls[3]["method"] == "GET"
    assert fixture.calls[3]["query"] == {"tab": ["liked"]}


def test_installed_dry_run_never_sends_a_wire_request(remote_loopback):
    fixture = remote_loopback
    draft = fixture.run(
        "draft", "create", "--title", "预览草稿", "--content", "正文", "--image", "https://example.org/draft.png",
    )["data"]
    commands = [
        ("publish", "image", "--title", "预览", "--content", "正文", "--image", "https://example.org/image.png"),
        ("publish", "video", "--title", "预览", "--content", "正文", "--video", "/backend/video.mp4", "--server-paths"),
        ("draft", "publish", draft["id"]),
        ("note", "like", NOTE_URL),
        ("note", "unlike", NOTE_URL),
        ("note", "favorite", NOTE_URL),
        ("note", "unfavorite", NOTE_URL),
        ("comment", "add", NOTE_URL, "--content", "评论"),
        ("comment", "reply", NOTE_URL, "--content", "回复", "--comment-id", COMMENT),
        ("notifications", "reply", COMMENT, "--content", "通知回复"),
        ("notifications", "like", COMMENT),
        ("notifications", "unlike", COMMENT),
        ("auth", "logout"),
    ]
    for command in commands:
        preview = fixture.run(*command, "--dry-run")["data"]
        assert preview["dry_run"] is True
        assert preview["submitted"] is False
        assert preview["request"]["method"] in {"POST", "DELETE"}
        assert fixture.calls == []
    assert fixture.run("draft", "show", draft["id"])["data"] == draft


def test_installed_write_disconnect_and_nested_failure_are_not_retried(remote_loopback):
    fixture = remote_loopback
    fixture.responses[("POST", "/api/v1/feeds/comment")] = None
    error = fixture.run("comment", "add", NOTE_URL, "--content", "只提交一次", expected=1)
    assert error["error"]["code"] == "BACKEND_OUTCOME_UNKNOWN"
    assert "未自动重试" in error["error"]["hint"]
    assert len(fixture.calls) == 1

    fixture.responses[("POST", "/api/v1/notifications/like")] = (
        200, {"success": True, "data": {"data": {"success": False, "message": "private-token"}}},
    )
    error = fixture.run("notifications", "like", COMMENT, expected=1)
    assert error["error"]["code"] == "BACKEND_ERROR"
    assert "private-token" not in json.dumps(error)
    assert len(fixture.calls) == 2
