"""CLI contracts tested without accessing an account or remote service."""

import base64
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.rednote import rednote_cli
from cli_anything.rednote.core.errors import RedNoteError

NOTE_ID = "0123456789abcdef01234567"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII="
)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("REDNOTE_HOME", str(tmp_path / "isolated-home"))
    for name in ("REDNOTE_BACKEND_URL", "REDNOTE_BACKEND_TOKEN", "REDNOTE_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    return CliRunner()


def invoke_json(runner, *args, expected=0, input=None):
    result = runner.invoke(rednote_cli.cli, ["--json", *args], input=input)
    assert result.exit_code == expected, result.output
    result_json = json.loads(result.stdout)
    assert result_json["ok"] is (expected == 0)
    return result_json["data"] if expected == 0 else result_json["error"]


@pytest.fixture
def fake_backend(monkeypatch):
    instances = []

    class FakeBackend:
        def __init__(self, base_url, timeout, token):
            self.options = {"base_url": base_url, "timeout": timeout, "token": token}
            self.calls = []
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True

        def health(self):
            return {"status": "ok"}

        def login_status(self):
            return {"is_logged_in": True}

        def login_qrcode(self):
            return {
                "is_logged_in": False,
                "img": "data:image/png;base64," + base64.b64encode(PNG).decode(),
            }

        def search(self, keyword, **filters):
            self.calls.append(("search", keyword, filters))
            return {"feeds": [{"id": NOTE_ID, "title": "咖啡"}]}

        def note(self, feed_id, token, **options):
            self.calls.append(("note", feed_id, token, options))
            return {"id": feed_id, "title": "笔记详情"}

        def profile(self, user_id, token):
            self.calls.append(("profile", user_id, token))
            return {"user_id": user_id}

        def feeds(self):
            return {"feeds": []}

        def me(self):
            return {"user_id": "self"}

    monkeypatch.setattr(rednote_cli, "Backend", FakeBackend)
    return FakeBackend, instances


def test_info_is_offline_and_uses_environment_home(runner, tmp_path, monkeypatch):
    def no_backend(*args, **kwargs):
        pytest.fail("offline info opened a backend")

    monkeypatch.setattr(rednote_cli, "Backend", no_backend)
    data = invoke_json(runner, "info")
    assert data["home"] == str((tmp_path / "isolated-home").resolve())
    assert {"search", "draft", "repl", "status"}.issubset(data["commands"])
    assert not (tmp_path / "isolated-home").exists()
    assert invoke_json(runner)["name"] == "cli-anything-rednote"


@pytest.mark.parametrize(
    "args",
    [
        ["nonexistent-command"],
        ["search"],
        ["search", "咖啡", "--sort", "wrong"],
        ["search", "  "],
        ["--timeout", "0", "info"],
        ["draft", "create"],
    ],
)
def test_usage_errors_are_single_json_objects(runner, args):
    error = invoke_json(runner, *args, expected=2)
    assert error["code"] == "USAGE_ERROR"
    assert error["message"]


def test_domain_error_is_structured_without_traceback(runner):
    error = invoke_json(runner, "note", "get", NOTE_ID, expected=1)
    assert error["code"] == "MISSING_XSEC_TOKEN"
    assert "hint" in error


def test_local_lifecycle_persists_across_invocations(runner, monkeypatch):
    def no_backend(*args, **kwargs):
        pytest.fail("local drafts opened a backend")

    monkeypatch.setattr(rednote_cli, "Backend", no_backend)
    created = invoke_json(
        runner,
        "draft",
        "create",
        "--title",
        "周末咖啡",
        "--content",
        "第一版",
        "--tag",
        "咖啡",
    )
    draft_id = created["id"]
    assert invoke_json(runner, "draft", "list") == [created]
    changed = invoke_json(
        runner, "draft", "update", draft_id, "--content", "第二版", "--clear-tags"
    )
    assert changed["content"] == "第二版"
    assert changed["tags"] == []
    invoke_json(runner, "draft", "undo")
    assert invoke_json(runner, "draft", "show", draft_id) == created
    invoke_json(runner, "draft", "redo")
    assert invoke_json(runner, "draft", "show", draft_id) == changed
    invoke_json(runner, "draft", "delete", draft_id)
    assert invoke_json(runner, "draft", "list") == []
    invoke_json(runner, "draft", "undo")
    assert invoke_json(runner, "draft", "show", draft_id) == changed


def test_explicit_home_overrides_environment_without_cross_contamination(
    runner, tmp_path
):
    first = invoke_json(runner, "draft", "create", "--title", "环境目录")
    other = tmp_path / "另一处 home"
    second = invoke_json(
        runner, "--home", str(other), "draft", "create", "--title", "独立目录"
    )
    assert invoke_json(runner, "draft", "list") == [first]
    assert invoke_json(runner, "--home", str(other), "draft", "list") == [second]


def test_utf8_bom_content_file_and_conflicting_content_inputs(runner, tmp_path):
    body = tmp_path / "正文.txt"
    body.write_text("第一行\n第二行 ☕", encoding="utf-8-sig")
    created = invoke_json(
        runner, "draft", "create", "--title", "中文", "--content-file", str(body)
    )
    assert created["content"] == "第一行\n第二行 ☕"
    error = invoke_json(
        runner,
        "draft",
        "create",
        "--title",
        "冲突",
        "--content",
        "text",
        "--content-file",
        str(body),
        expected=2,
    )
    assert error["code"] == "USAGE_ERROR"
    assert len(invoke_json(runner, "draft", "list")) == 1


@pytest.mark.parametrize(
    "options",
    [[], ["--tag", "tag", "--clear-tags"], ["--image", "photo.png", "--clear-images"]],
)
def test_invalid_update_does_not_mutate(runner, options):
    created = invoke_json(runner, "draft", "create", "--title", "不应改变")
    invoke_json(runner, "draft", "update", created["id"], *options, expected=2)
    assert invoke_json(runner, "draft", "show", created["id"]) == created


def test_export_writes_actual_utf8_file_and_preserves_existing_file(runner, tmp_path):
    created = invoke_json(
        runner,
        "draft",
        "create",
        "--title",
        "中文标题",
        "--content",
        "正文 ☕",
        "--tag",
        "生活",
    )
    target = tmp_path / "笔记.md"
    exported = invoke_json(
        runner, "draft", "export", created["id"], "--output", str(target)
    )
    assert Path(exported["path"]) == target.resolve()
    assert target.read_text(encoding="utf-8") == "# 中文标题\n\n正文 ☕\n\n#生活\n"
    before = target.read_bytes()
    error = invoke_json(
        runner, "draft", "export", created["id"], "--output", str(target), expected=1
    )
    assert error["code"] == "FILE_EXISTS"
    assert target.read_bytes() == before


def test_config_redacts_token_and_honors_environment(runner, monkeypatch):
    monkeypatch.setenv("REDNOTE_BACKEND_URL", "http://127.0.0.1:18222")
    monkeypatch.setenv("REDNOTE_TIMEOUT", "7.5")
    monkeypatch.setenv("REDNOTE_BACKEND_TOKEN", "secret-do-not-echo")
    data = invoke_json(runner, "config", "show")
    assert data["backend_url"] == "http://127.0.0.1:18222"
    assert data["timeout"] == 7.5
    assert data["token_configured"] is True
    assert "secret-do-not-echo" not in json.dumps(data)


def test_status_unavailable_is_nonzero_and_closes_backend(
    runner, fake_backend, monkeypatch
):
    cls, instances = fake_backend

    def unavailable(self):
        raise RedNoteError("BACKEND_UNAVAILABLE", "Cannot connect", "Start the backend")

    monkeypatch.setattr(cls, "health", unavailable)
    error = invoke_json(runner, "status", expected=1)
    assert error["code"] == "BACKEND_UNAVAILABLE"
    assert instances[-1].closed


def test_status_success_and_options_are_forwarded(runner, fake_backend, monkeypatch):
    _, instances = fake_backend
    monkeypatch.setenv("REDNOTE_BACKEND_TOKEN", "test-token")
    data = invoke_json(
        runner, "--backend-url", "http://localhost:18333", "--timeout", "9", "status"
    )
    assert data == {"health": {"status": "ok"}, "login": {"is_logged_in": True}}
    assert instances[-1].options == {
        "base_url": "http://localhost:18333",
        "timeout": 9.0,
        "token": "test-token",
    }
    assert instances[-1].closed


def test_search_filters_are_forwarded(runner, fake_backend):
    _, instances = fake_backend
    data = invoke_json(
        runner,
        "search",
        "咖啡 店",
        "--sort",
        "最新",
        "--type",
        "图文",
        "--time",
        "一周内",
    )
    assert data["feeds"][0]["id"] == NOTE_ID
    assert instances[-1].calls == [
        (
            "search",
            "咖啡 店",
            {"sort_by": "最新", "note_type": "图文", "publish_time": "一周内"},
        )
    ]
    assert instances[-1].closed


def test_note_and_user_reference_plumbing(runner, fake_backend):
    _, instances = fake_backend
    invoke_json(
        runner,
        "note",
        "get",
        f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=url%2Btoken",
        "--all-comments",
    )
    assert instances[-1].calls == [
        ("note", NOTE_ID, "url+token", {"load_all_comments": True})
    ]
    invoke_json(
        runner,
        "user",
        "get",
        f"https://www.xiaohongshu.com/user/profile/{NOTE_ID}?xsec_token=stale",
        "--xsec-token",
        "fresh-token",
    )
    assert instances[-1].calls == [("profile", NOTE_ID, "fresh-token")]


def test_feed_and_current_user(runner, fake_backend):
    assert invoke_json(runner, "feed", "list") == {"feeds": []}
    assert invoke_json(runner, "user", "me") == {"user_id": "self"}
    assert invoke_json(runner, "auth", "status") == {"is_logged_in": True}


def test_qrcode_saved_as_png_without_echoing_base64(runner, fake_backend, tmp_path):
    target = tmp_path / "登录二维码.png"
    data = invoke_json(runner, "auth", "qrcode", "--output", str(target))
    assert target.read_bytes() == PNG
    assert "img" not in data
    assert data["path"] == str(target.resolve())
    error = invoke_json(runner, "auth", "qrcode", "--output", str(target), expected=1)
    assert error["code"] == "FILE_EXISTS"
    assert target.read_bytes() == PNG


@pytest.mark.parametrize(
    "encoded", ["not base64!", base64.b64encode(b"not a PNG").decode()]
)
def test_invalid_qrcode_is_not_written(
    runner, fake_backend, monkeypatch, tmp_path, encoded
):
    cls, _ = fake_backend
    monkeypatch.setattr(
        cls, "login_qrcode", lambda self: {"is_logged_in": False, "img": encoded}
    )
    target = tmp_path / "invalid.png"
    error = invoke_json(runner, "auth", "qrcode", "--output", str(target), expected=1)
    assert error["code"] == "INVALID_RESPONSE"
    assert not target.exists()


def test_already_logged_in_does_not_create_qrcode(
    runner, fake_backend, monkeypatch, tmp_path
):
    cls, _ = fake_backend
    monkeypatch.setattr(cls, "login_qrcode", lambda self: {"is_logged_in": True})
    target = tmp_path / "unused.png"
    assert invoke_json(runner, "auth", "qrcode", "--output", str(target)) == {
        "is_logged_in": True
    }
    assert not target.exists()


def test_repl_piped_json_preserves_quotes_windows_paths_and_home(runner):
    commands = 'draft create --title "周末 笔记" --content \'含 "引号" 的正文\' --image "C:\\相片 目录\\图片.png"\ndraft list\nquit\n'
    result = runner.invoke(rednote_cli.cli, ["--json", "repl"], input=commands)
    assert result.exit_code == 0, result.output
    outputs = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(outputs) == 2
    created = outputs[0]["data"]
    assert created["title"] == "周末 笔记"
    assert created["content"] == '含 "引号" 的正文'
    assert created["images"] == [r"C:\相片 目录\图片.png"]
    assert outputs[1]["data"] == [created]


def test_repl_recovers_from_bad_quotes_and_reports_failed_batch(runner):
    result = runner.invoke(
        rednote_cli.cli, ["--json", "repl"], input='search "unfinished\ninfo\nquit\n'
    )
    assert result.exit_code == 1, result.output
    outputs = [json.loads(line) for line in result.stdout.splitlines()]
    assert outputs[0]["error"]["code"] == "USAGE_ERROR"
    assert outputs[1]["data"]["name"] == "cli-anything-rednote"


def test_repl_returns_nonzero_for_command_error_but_continues(runner):
    result = runner.invoke(
        rednote_cli.cli, ["--json", "repl"], input="unknown-command\nhelp\nexit\n"
    )
    assert result.exit_code == 1, result.output
    outputs = [json.loads(line) for line in result.stdout.splitlines()]
    assert outputs[0]["error"]["code"] == "USAGE_ERROR"
    assert outputs[1]["ok"] is True


@pytest.mark.parametrize("nested", ["repl", "--json repl"])
def test_repl_rejects_nested_sessions_even_with_global_options(runner, nested):
    result = runner.invoke(
        rednote_cli.cli,
        ["--json", "repl"],
        input=f"{nested}\ninfo\nquit\n",
    )
    assert result.exit_code == 1, result.output
    outputs = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(outputs) == 2
    assert outputs[0]["error"]["code"] == "USAGE_ERROR"
    assert outputs[1]["data"]["name"] == "cli-anything-rednote"
