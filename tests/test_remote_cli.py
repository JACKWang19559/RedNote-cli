"""Remote CLI workflows with no external account or network access."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from cli_anything.rednote import rednote_cli
from cli_anything.rednote.core.drafts import DraftStore

NOTE = "680000000000000000000001"
USER = "580000000000000000000002"
COMMENT = "680000000000000000000003"
REF = f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=token%2Bvalue"


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.setenv("REDNOTE_HOME", str(tmp_path / "home"))
    for name in ("REDNOTE_BACKEND_URL", "REDNOTE_BACKEND_TOKEN", "REDNOTE_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    runner = CliRunner()

    def run(*args, expected=0):
        result = runner.invoke(rednote_cli.cli, ["--json", *args])
        assert result.exit_code == expected, result.output
        value = json.loads(result.stdout)
        assert value["ok"] == (expected == 0)
        return value["data"] if not expected else value["error"]

    return run


@pytest.fixture
def calls(monkeypatch):
    calls = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

        def __getattr__(self, name):
            def invoke(*args, **kwargs):
                calls.append((name, args, kwargs))
                return {"recorded": name}

            return invoke

    monkeypatch.setattr(rednote_cli, "Backend", Recorder)
    return calls


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run or invalid input constructed a backend client")

    monkeypatch.setattr(rednote_cli, "Backend", forbidden)


WRITE_CASES = [
    (["auth", "logout"], "DELETE", "/api/v1/login/cookies"),
    (["note", "like", REF], "POST", "/api/v1/feeds/like"),
    (["note", "unlike", REF], "POST", "/api/v1/feeds/like"),
    (["note", "favorite", REF], "POST", "/api/v1/feeds/favorite"),
    (["note", "unfavorite", REF], "POST", "/api/v1/feeds/favorite"),
    (["comment", "add", REF, "--content", "测试评论"], "POST", "/api/v1/feeds/comment"),
    (
        ["comment", "reply", REF, "--content", "回复", "--comment-id", COMMENT],
        "POST",
        "/api/v1/feeds/comment/reply",
    ),
    (
        ["notifications", "reply", COMMENT, "--content", "回复"],
        "POST",
        "/api/v1/notifications/reply",
    ),
    (["notifications", "like", COMMENT], "POST", "/api/v1/notifications/like"),
    (["notifications", "unlike", COMMENT], "POST", "/api/v1/notifications/like"),
    (
        [
            "publish",
            "image",
            "--title",
            "图文",
            "--content",
            "正文",
            "--image",
            "https://example.org/cover.jpg",
        ],
        "POST",
        "/api/v1/publish",
    ),
    (
        [
            "publish",
            "video",
            "--title",
            "视频",
            "--content",
            "正文",
            "--video",
            "/backend/clip.mp4",
            "--server-paths",
        ],
        "POST",
        "/api/v1/publish_video",
    ),
]


@pytest.mark.parametrize("args,method,path", WRITE_CASES)
def test_every_remote_write_has_offline_dry_run(cli, offline, args, method, path):
    preview = cli(*args, "--dry-run")
    assert preview["submitted"] is False
    assert preview["dry_run"] is True
    assert preview["request"]["method"] == method
    assert preview["request"]["path"] == path


@pytest.mark.parametrize(
    "command,backend_method,flag_name,flag",
    [
        ("like", "like", "unlike", False),
        ("unlike", "like", "unlike", True),
        ("favorite", "favorite", "unfavorite", False),
        ("unfavorite", "favorite", "unfavorite", True),
    ],
)
def test_explicit_note_action_executes_correct_direction(
    cli, calls, command, backend_method, flag_name, flag
):
    cli("note", command, REF)
    assert calls == [(backend_method, (NOTE, "token+value"), {flag_name: flag})]


def test_publish_full_metadata_and_local_file_normalization(cli, calls, tmp_path):
    cover = tmp_path / "中文 封面.jpg"
    cover.write_bytes(b"fixture-media-validated-by-backend")
    content = tmp_path / "正文.txt"
    content.write_text("第一行\n第二行", encoding="utf-8-sig")
    schedule = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    cli(
        "publish",
        "image",
        "--title",
        "发布测试",
        "--content-file",
        str(content),
        "--image",
        str(cover),
        "--tag",
        "#咖啡",
        "--tag",
        "咖啡",
        "--original",
        "--visibility",
        "仅自己可见",
        "--schedule-at",
        schedule,
        "--product",
        "咖啡豆",
    )
    method, args, kwargs = calls[0]
    assert method == "publish" and kwargs == {}
    assert args[0] == {
        "title": "发布测试",
        "content": "第一行\n第二行",
        "images": [str(cover.resolve())],
        "tags": ["咖啡"],
        "products": ["咖啡豆"],
        "is_original": True,
        "visibility": "仅自己可见",
        "schedule_at": schedule,
    }


def test_draft_publish_preview_and_commit_preserve_local_document(cli, calls):
    created = cli(
        "draft",
        "create",
        "--title",
        "本地草稿",
        "--content",
        "草稿内容",
        "--image",
        "https://example.org/cover.jpg",
        "--tag",
        "咖啡",
    )
    preview = cli("draft", "publish", created["id"], "--dry-run")
    assert not calls
    assert preview["request"]["body"]["content"] == "草稿内容"
    cli("draft", "publish", created["id"])
    assert calls[0][0] == "publish"
    assert cli("draft", "show", created["id"]) == created
    # Publishing must not add a local undo event.
    cli("draft", "undo")
    assert cli("draft", "list") == []


def test_video_draft_explicitly_overrides_images_and_rejects_original(cli, calls):
    created = cli(
        "draft",
        "create",
        "--title",
        "视频草稿",
        "--content",
        "正文",
        "--image",
        "unused.jpg",
    )
    cli(
        "draft",
        "publish",
        created["id"],
        "--video",
        r"D:\backend\clip.mp4",
        "--server-paths",
    )
    assert calls[0][0] == "publish_video"
    assert calls[0][1][0]["video"] == r"D:\backend\clip.mp4"
    assert "images" not in calls[0][1][0]
    calls.clear()
    cli(
        "draft",
        "publish",
        created["id"],
        "--video",
        "/clip.mp4",
        "--server-paths",
        "--original",
        expected=1,
    )
    assert calls == []


def test_comment_reply_exact_targets_and_content_file(cli, calls, tmp_path):
    body = tmp_path / "comment.txt"
    body.write_text("谢谢\n很有帮助", encoding="utf-8")
    cli(
        "comment",
        "reply",
        REF,
        "--content-file",
        str(body),
        "--comment-id",
        COMMENT,
        "--user-id",
        USER,
    )
    assert calls == [
        (
            "reply_comment",
            (NOTE, "token+value", "谢谢\n很有帮助"),
            {"comment_id": COMMENT, "user_id": USER},
        )
    ]


def test_notifications_and_logout_dispatch(cli, calls):
    cli("notifications", "unread")
    cli("notifications", "list", "--tab", "connections", "--limit", "7")
    cli("notifications", "reply", COMMENT, "--content", "谢谢")
    cli("notifications", "like", COMMENT)
    cli("notifications", "unlike", COMMENT)
    cli("auth", "logout")
    assert calls == [
        ("notifications_unread", (), {}),
        ("notifications", (), {"tab": "connections", "limit": 7}),
        ("notification_reply", (COMMENT, "谢谢"), {}),
        ("notification_like", (COMMENT,), {"unlike": False}),
        ("notification_like", (COMMENT,), {"unlike": True}),
        ("delete_cookies", (), {}),
    ]


def test_all_extended_read_options(cli, calls):
    cli("search", "咖啡", "--scope", "已关注", "--location", "同城")
    cli(
        "note",
        "get",
        REF,
        "--comment-limit",
        "30",
        "--expand-replies",
        "--reply-limit",
        "5",
        "--scroll-speed",
        "slow",
    )
    cli("user", "get", USER, "--xsec-token", "user-token", "--tab", "fav")
    cli("user", "me", "--tab", "liked")
    assert calls[0][2]["search_scope"] == "已关注"
    assert calls[0][2]["location"] == "同城"
    assert calls[1] == (
        "note",
        (NOTE, "token+value"),
        {
            "load_all_comments": True,
            "comment_config": {
                "max_comment_items": 30,
                "click_more_replies": True,
                "max_replies_threshold": 5,
                "scroll_speed": "slow",
            },
        },
    )
    assert calls[2] == ("profile", (USER, "user-token"), {"tab": "fav"})
    assert calls[3] == ("me", (), {"tab": "liked"})


@pytest.mark.parametrize(
    "args,exit_code",
    [
        (["comment", "reply", REF, "--content", "回复"], 2),
        (["comment", "add", REF, "--content", " "], 2),
        (["comment", "add", REF, "--content", "bad\x00text"], 1),
        (["notifications", "reply", " ", "--content", "回复"], 2),
        (["notifications", "like", "comment with spaces"], 2),
        (["notifications", "list", "--limit", "0"], 2),
        (["note", "get", REF, "--reply-limit", "2"], 2),
        (
            [
                "publish",
                "image",
                "--title",
                "测试",
                "--image",
                "https://example.org/x.jpg",
            ],
            2,
        ),
        (
            [
                "publish",
                "video",
                "--title",
                "测试",
                "--content",
                "正文",
                "--video",
                "https://example.org/clip.mp4",
            ],
            1,
        ),
        (
            [
                "publish",
                "image",
                "--title",
                "测试",
                "--content",
                "正文",
                "--image",
                "relative.jpg",
                "--server-paths",
            ],
            1,
        ),
    ],
)
def test_invalid_remote_commands_fail_without_network(cli, offline, args, exit_code):
    error = cli(*args, expected=exit_code)
    assert error["code"]


def test_capabilities_is_offline_and_honest_about_platform_gaps(cli, offline):
    data = cli("capabilities")
    assert data["runtime_probed"] is False
    assert len(data["operations"]) == 19
    assert {item["feature"] for item in data["unsupported"]} >= {
        "私信",
        "关注/取消关注",
        "编辑/删除已发布笔记",
    }


def test_draft_relative_images_stay_bound_across_working_directories(
    cli, calls, tmp_path, monkeypatch
):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "cover.jpg").write_bytes(b"intended image")
    (second / "cover.jpg").write_bytes(b"different image with same name")
    monkeypatch.chdir(first)
    created = cli(
        "draft",
        "create",
        "--title",
        "路径绑定",
        "--content",
        "正文",
        "--image",
        "cover.jpg",
    )
    assert created["images"] == [str(first / "cover.jpg")]
    monkeypatch.chdir(second)
    preview = cli("draft", "publish", created["id"], "--dry-run")
    assert preview["request"]["body"]["images"] == [str(first / "cover.jpg")]
    assert calls == []
    cli("draft", "publish", created["id"])
    assert calls[0][1][0]["images"] == [str(first / "cover.jpg")]
    updated = cli("draft", "update", created["id"], "--image", "cover.jpg")
    assert updated["images"] == [str(second / "cover.jpg")]
    monkeypatch.chdir(first)
    preview = cli("draft", "publish", created["id"], "--dry-run")
    assert preview["request"]["body"]["images"] == [str(second / "cover.jpg")]


def test_legacy_relative_draft_image_requires_explicit_rebinding(
    cli, offline, tmp_path, monkeypatch
):
    created = DraftStore(tmp_path / "home").create(
        "旧版草稿", "正文", images=["cover.jpg"]
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cover.jpg").write_bytes(b"possibly wrong file")
    error = cli("draft", "publish", created["id"], "--dry-run", expected=1)
    assert error["code"] == "AMBIGUOUS_DRAFT_MEDIA"
    cli("draft", "update", created["id"], "--image", "cover.jpg")
    assert cli("draft", "publish", created["id"], "--dry-run")["submitted"] is False


def test_draft_images_can_reference_future_files_and_other_os_absolute_paths(
    cli, tmp_path, offline
):
    created = cli(
        "draft",
        "create",
        "--title",
        "稍后配图",
        "--image",
        "future.jpg",
        "--image",
        r"C:\backend\cover.jpg",
        "--image",
        "/app/images/cover.jpg",
        "--image",
        "https://example.org/cover.jpg",
    )
    assert created["images"][0].endswith("future.jpg")
    assert created["images"][1:] == [
        r"C:\backend\cover.jpg",
        "/app/images/cover.jpg",
        "https://example.org/cover.jpg",
    ]
