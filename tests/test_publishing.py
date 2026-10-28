"""Publication validation is offline; no tests publish content to an account."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli_anything.rednote.core.errors import RedNoteError
from cli_anything.rednote.core.publishing import (
    build_publish_payload,
    title_length,
    validate_publish_payload,
)

NOW = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
IMAGE_URL = "https://example.test/图片.jpg?version=1"


def image_payload(**changes):
    return {
        "title": "咖啡地图",
        "content": "正文内容",
        "images": [IMAGE_URL],
        **changes,
    }


def test_image_builder_resolves_local_files_and_normalizes_options(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "中文 图片.png"
    local.write_bytes(b"a nonempty fixture; no format detection promised")
    result = build_publish_payload(
        " 咖啡地图 ",
        "第一行\n第二行 ☕",
        images=[local.name, IMAGE_URL],
        tags=[" #咖啡 ", "探店", "咖啡"],
        products=[" 咖啡豆 ", "咖啡豆", "杯子"],
        is_original=True,
        visibility="仅自己可见",
        schedule_at="2026-10-28T18:00:00+08:00",
        now=NOW,
    )
    assert result == {
        "title": "咖啡地图",
        "content": "第一行\n第二行 ☕",
        "images": [str(local.resolve()), IMAGE_URL],
        "tags": ["咖啡", "探店"],
        "products": ["咖啡豆", "杯子"],
        "is_original": True,
        "visibility": "仅自己可见",
        "schedule_at": "2026-10-28T18:00:00+08:00",
    }


def test_video_builder_uses_only_video_fields(tmp_path):
    video = tmp_path / "视频.mp4"
    video.write_bytes(b"fixture")
    result = build_publish_payload("视频日记", "今天的故事", video=str(video), tags=[])
    assert result == {
        "title": "视频日记",
        "content": "今天的故事",
        "visibility": "公开可见",
        "video": str(video.resolve()),
        "tags": [],
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        ("hello", 3),
        ("12345", 3),
        ("OOTD穿搭分享", 6),
        ("字" * 20, 20),
        ("a" * 40, 20),
        ("😀", 2),
        ("今天好开心😀", 7),
        ("a", 1),
        ("ab", 1),
        ("abc", 2),
        ("！？", 2),
        ("!?", 1),
    ],
)
def test_title_length_matches_upstream_examples(value, expected):
    assert title_length(value) == expected


@pytest.mark.parametrize("title", ["a" * 40, "字" * 20, "😀" * 10])
def test_title_boundary_accepts_upstream_limit(title):
    assert validate_publish_payload(image_payload(title=title))["title"] == title


@pytest.mark.parametrize(
    "title",
    [
        "a" * 41,
        "字" * 21,
        "😀" * 11,
        "两\n行",
        "两\r行",
        "两\u2028行",
        "\ud800",
        "",
        " ",
        None,
    ],
)
def test_invalid_title_rejected(title):
    with pytest.raises(RedNoteError) as exc:
        validate_publish_payload(image_payload(title=title))
    assert exc.value.code == "INVALID_PUBLISH"


@pytest.mark.parametrize("content", ["", " \n ", None, 12, "\x00", "\udfff"])
def test_invalid_content_rejected(content):
    with pytest.raises(RedNoteError):
        validate_publish_payload(image_payload(content=content))


def test_content_preserved_without_inventing_server_length_limit():
    content = "正文\n" + "字" * 1500
    assert (
        validate_publish_payload(image_payload(content=content))["content"] == content
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("tags", "tag"),
        ("tags", ["#"]),
        ("tags", [""]),
        ("tags", [12]),
        ("products", "product"),
        ("products", [None]),
        ("products", ["\n"]),
        ("is_original", "true"),
        ("is_original", 1),
        ("visibility", "private"),
        ("images", "image.jpg"),
        ("images", []),
        ("images", [None]),
        ("images", [""]),
        ("images", {"path": "image.jpg"}),
    ],
)
def test_invalid_field_types_and_values(field, value):
    with pytest.raises(RedNoteError):
        validate_publish_payload(image_payload(**{field: value}))


def test_too_many_distinct_tags_rejected_without_silent_truncation():
    tags = [f"标签{number}" for number in range(11)]
    with pytest.raises(RedNoteError):
        validate_publish_payload(image_payload(tags=tags))
    result = validate_publish_payload(image_payload(tags=tags[:10] + [tags[0]]))
    assert len(result["tags"]) == 10


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("image", {"video": "/remote/video.mp4"}),
        ("image", {"unknown": True}),
        ("video", {"is_original": False}),
        ("video", {"images": []}),
    ],
)
def test_unknown_or_wrong_kind_fields_are_never_silently_dropped(kind, extra):
    payload = (
        image_payload()
        if kind == "image"
        else {"title": "视频", "content": "内容", "video": "/remote/video.mp4"}
    )
    with pytest.raises(RedNoteError):
        validate_publish_payload({**payload, **extra}, kind=kind)


def test_structural_validation_never_inspects_local_files_or_mutates_input(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure payload validation must not inspect the local filesystem")

    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    payload = image_payload(images=["/remote/not-local.jpg"], tags=["#咖啡", "咖啡"])
    original = deepcopy(payload)
    result = validate_publish_payload(payload)
    assert result["tags"] == ["咖啡"]
    assert payload == original
    video = {"title": "视频", "content": "内容", "video": r"Z:\remote\not-local.mp4"}
    assert validate_publish_payload(video, kind="video") == video
    # The service checks its own time window; this layer is structural and pure.
    assert (
        validate_publish_payload(image_payload(schedule_at="2020-01-01T00:00:00Z"))[
            "schedule_at"
        ]
        == "2020-01-01T00:00:00Z"
    )


@pytest.mark.parametrize(
    "path",
    ["/app/images/图 片.jpg", r"D:\pictures\图 片.jpg", r"\\server\share\photo.jpg"],
)
def test_server_paths_preserved_across_operating_systems(path, monkeypatch):
    monkeypatch.setattr(
        Path, "stat", lambda *_: pytest.fail("No local stat for server paths")
    )
    result = build_publish_payload("标题", "正文", images=[path], server_paths=True)
    assert result["images"] == [path]


@pytest.mark.parametrize(
    "path", ["relative.jpg", "~/photo.jpg", r"C:photo.jpg", r"\photo.jpg"]
)
def test_server_paths_must_be_absolute(path):
    with pytest.raises(RedNoteError) as exc:
        build_publish_payload("标题", "正文", images=[path], server_paths=True)
    assert exc.value.code == "INVALID_MEDIA"


def test_missing_empty_and_directory_local_media_rejected(tmp_path):
    empty = tmp_path / "empty.jpg"
    empty.touch()
    for path in (tmp_path / "missing.jpg", empty, tmp_path):
        with pytest.raises(RedNoteError) as exc:
            build_publish_payload("标题", "正文", images=[str(path)])
        assert exc.value.code == "INVALID_MEDIA"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/image.jpg",
        "https://u:secret@example.test/photo.jpg",
        "https://example.test:invalid/image.jpg",
        "https://example.test:0/image.jpg",
        "https://example.test/a b.jpg",
        "https://example.test/\nimage.jpg",
        "https://example.test\\evil/image.jpg",
        "http:///missing-host.jpg",
    ],
)
def test_invalid_image_urls_rejected_without_echoing_credentials(url):
    with pytest.raises(RedNoteError) as exc:
        validate_publish_payload(image_payload(images=[url]))
    assert "secret" not in str(exc.value)


def test_video_cannot_be_remote_url_or_mix_image_only_fields():
    with pytest.raises(RedNoteError):
        build_publish_payload("标题", "正文", video="https://example.test/video.mp4")
    for extra in ({"images": []}, {"is_original": True}):
        with pytest.raises(RedNoteError):
            build_publish_payload(
                "标题", "正文", video="/remote/video.mp4", server_paths=True, **extra
            )


@pytest.mark.parametrize("delta", [timedelta(hours=1), timedelta(days=14)])
def test_schedule_inclusive_bounds(delta):
    scheduled = (NOW + delta).isoformat()
    result = build_publish_payload(
        "标题", "正文", images=[IMAGE_URL], schedule_at=scheduled, now=NOW
    )
    assert result["schedule_at"] == scheduled


@pytest.mark.parametrize(
    "delta",
    [
        timedelta(minutes=59, seconds=59),
        timedelta(days=14, seconds=1),
        timedelta(days=-1),
    ],
)
def test_schedule_outside_window(delta):
    with pytest.raises(RedNoteError) as exc:
        build_publish_payload(
            "标题",
            "正文",
            images=[IMAGE_URL],
            schedule_at=(NOW + delta).isoformat(),
            now=NOW,
        )
    assert exc.value.code == "INVALID_SCHEDULE"


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-28T10:00:00",
        "2026-10-28",
        "2026-10-28 10:00:00Z",
        "2026-10-28T10:00:00+00:99",
        "2026-10-28T10:00:00+24:00",
        "2026-02-30T10:00:00Z",
        "",
        "tomorrow",
        123,
    ],
)
def test_schedule_requires_valid_explicit_timezone_rfc3339(value):
    with pytest.raises(RedNoteError) as exc:
        build_publish_payload(
            "标题", "正文", images=[IMAGE_URL], schedule_at=value, now=NOW
        )
    assert exc.value.code == "INVALID_SCHEDULE"


def test_schedule_preserves_offset_and_accepts_z():
    for value in ("2026-10-28T18:00:00+08:00", "2026-10-28T10:00:00Z"):
        assert (
            build_publish_payload(
                "标题", "正文", images=[IMAGE_URL], schedule_at=value, now=NOW
            )["schedule_at"]
            == value
        )
    with pytest.raises(RedNoteError):
        build_publish_payload(
            "标题",
            "正文",
            images=[IMAGE_URL],
            schedule_at="2026-10-28T10:00:00Z",
            now=NOW.replace(tzinfo=None),
        )


@pytest.mark.parametrize(
    "offset", ["+00:60", "+01:99", "-00:60", "-01:99", "+24:00", "-24:00"]
)
def test_pure_schedule_validation_rejects_offsets_python_would_normalize(offset):
    with pytest.raises(RedNoteError) as exc:
        validate_publish_payload(
            image_payload(schedule_at=f"2026-10-28T10:00:00{offset}")
        )
    assert exc.value.code == "INVALID_SCHEDULE"


@pytest.mark.parametrize("value", [[], None, "payload"])
def test_payload_must_be_object(value):
    with pytest.raises(RedNoteError):
        validate_publish_payload(value)


@pytest.mark.parametrize("kind", [[], None, "unknown"])
def test_unknown_kind_has_domain_error(kind):
    with pytest.raises(RedNoteError):
        validate_publish_payload(image_payload(), kind=kind)
