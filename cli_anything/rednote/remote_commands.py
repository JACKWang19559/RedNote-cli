"""Remote write commands; dry runs never construct a backend client."""

from __future__ import annotations

import click

from .core.draft_media import require_bound_draft_images
from .core.errors import RedNoteError
from .core.publishing import build_publish_payload
from .core.refs import parse_reference


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise click.UsageError(f"{label}不能为空。")
    if any(
        (ord(c) < 32 and c not in "\r\n\t")
        or ord(c) == 127
        or 0xD800 <= ord(c) <= 0xDFFF
        for c in value
    ):
        raise RedNoteError(
            "INVALID_ARGUMENT", f"{label}包含不支持的控制字符或 Unicode 字符。"
        )
    return value


def _id(value, label):
    value = _text(value, label)
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise click.UsageError(f"{label}不能包含空白或控制字符。")
    return value


def dry_run_option(function):
    return click.option(
        "--dry-run", is_flag=True, help="校验并预览请求，不连接后端、不提交。"
    )(function)


def reference_options(function):
    function = click.option("--xsec-token", help="裸笔记 ID 对应的 xsec_token。")(
        function
    )
    return click.argument("reference")(function)


def content_options(function):
    function = click.option("--content", help="正文或评论内容。")(function)
    return click.option(
        "--content-file",
        type=click.Path(exists=True, dir_okay=False),
        help="UTF-8 正文文件，与 --content 二选一。",
    )(function)


def publish_options(function):
    for decorator in [
        click.option(
            "--schedule-at", help="ISO8601 时间，必须带时区，未来 1 小时至 14 天。"
        ),
        click.option(
            "--visibility",
            type=click.Choice(["公开可见", "仅自己可见", "仅互关好友可见"]),
            default="公开可见",
            show_default=True,
        ),
        click.option(
            "--product",
            "products",
            multiple=True,
            help="商品搜索关键词，可重复；后端自动选择首个匹配项。",
        ),
        click.option(
            "--server-paths",
            is_flag=True,
            help="媒体路径位于后端主机；只校验绝对路径，不检查本机文件。",
        ),
        dry_run_option,
    ]:
        function = decorator(function)
    return function


def register_remote_commands(
    cli, auth, note, draft, backend, drafts, emit, read_content
):
    """Register with injected context helpers, avoiding circular imports."""

    def dispatch(operation, method, path, payload, call, dry_run):
        if dry_run:
            emit(
                {
                    "dry_run": True,
                    "operation": operation,
                    "request": {"method": method, "path": path, "body": payload},
                    "submitted": False,
                }
            )
        else:
            emit(call())

    def content_text(content, content_file):
        from pathlib import Path

        return _text(
            read_content(content, Path(content_file) if content_file else None), "内容"
        )

    @auth.command("logout")
    @dry_run_option
    def logout(dry_run):
        """删除后端保存的登录 Cookies；下次操作需重新扫码。"""
        dispatch(
            "auth.logout",
            "DELETE",
            "/api/v1/login/cookies",
            None,
            lambda: backend().delete_cookies(),
            dry_run,
        )

    @cli.group()
    def publish():
        """发布图文或视频；--dry-run 可先查看完整请求。"""

    def send_publish(payload, kind, dry_run, operation=None):
        path = "/api/v1/publish_video" if kind == "video" else "/api/v1/publish"
        dispatch(
            operation or f"publish.{kind}",
            "POST",
            path,
            payload,
            lambda: (
                backend().publish_video(payload)
                if kind == "video"
                else backend().publish(payload)
            ),
            dry_run,
        )

    @publish.command("image")
    @click.option("--title", required=True)
    @content_options
    @click.option(
        "--image",
        "images",
        multiple=True,
        required=True,
        help="图片 URL 或后端可访问的文件，可重复。",
    )
    @click.option("--tag", "tags", multiple=True)
    @click.option("--original", is_flag=True, help="声明原创。")
    @publish_options
    def publish_image(
        title,
        content,
        content_file,
        images,
        tags,
        original,
        schedule_at,
        visibility,
        products,
        server_paths,
        dry_run,
    ):
        """发布图文笔记。"""
        payload = build_publish_payload(
            title,
            content_text(content, content_file),
            images=list(images),
            tags=list(tags),
            is_original=original,
            schedule_at=schedule_at,
            visibility=visibility,
            products=list(products),
            server_paths=server_paths,
        )
        send_publish(payload, "image", dry_run)

    @publish.command("video")
    @click.option("--title", required=True)
    @content_options
    @click.option("--video", required=True, help="一个后端可访问的视频文件路径。")
    @click.option("--tag", "tags", multiple=True)
    @publish_options
    def publish_video(
        title,
        content,
        content_file,
        video,
        tags,
        schedule_at,
        visibility,
        products,
        server_paths,
        dry_run,
    ):
        """发布视频笔记。"""
        payload = build_publish_payload(
            title,
            content_text(content, content_file),
            video=video,
            tags=list(tags),
            schedule_at=schedule_at,
            visibility=visibility,
            products=list(products),
            server_paths=server_paths,
        )
        send_publish(payload, "video", dry_run)

    @draft.command("publish")
    @click.argument("draft_id")
    @click.option("--video", help="改用指定视频发布；不会上传草稿中的图片引用。")
    @click.option("--original", is_flag=True, help="图文声明原创；视频不支持。")
    @publish_options
    def draft_publish(
        draft_id,
        video,
        original,
        schedule_at,
        visibility,
        products,
        server_paths,
        dry_run,
    ):
        """将本地草稿内容发布到小红书；保留草稿，undo 无法撤销发布。"""
        document = drafts().get(draft_id)
        payload = build_publish_payload(
            document["title"],
            document["content"],
            images=None if video else require_bound_draft_images(document["images"]),
            video=video,
            tags=document["tags"],
            is_original=original,
            schedule_at=schedule_at,
            visibility=visibility,
            products=list(products),
            server_paths=server_paths,
        )
        send_publish(
            payload, "video" if video else "image", dry_run, operation="draft.publish"
        )

    def add_note_action(name, backend_method, flag_name, flag):
        @note.command(name)
        @reference_options
        @dry_run_option
        def action(reference, xsec_token, dry_run):
            feed_id, token = parse_reference(reference, xsec_token=xsec_token)
            payload = {"feed_id": feed_id, "xsec_token": token, flag_name: flag}
            dispatch(
                f"note.{name}",
                "POST",
                f"/api/v1/feeds/{backend_method}",
                payload,
                lambda: getattr(backend(), backend_method)(
                    feed_id, token, **{flag_name: flag}
                ),
                dry_run,
            )

        action.help = {
            "like": "点赞笔记。",
            "unlike": "取消笔记点赞。",
            "favorite": "收藏笔记。",
            "unfavorite": "取消笔记收藏。",
        }[name]

    for name, method, flag_name, flag in [
        ("like", "like", "unlike", False),
        ("unlike", "like", "unlike", True),
        ("favorite", "favorite", "unfavorite", False),
        ("unfavorite", "favorite", "unfavorite", True),
    ]:
        add_note_action(name, method, flag_name, flag)

    @cli.group()
    def comment():
        """发表或回复笔记评论。"""

    @comment.command("add")
    @reference_options
    @content_options
    @dry_run_option
    def comment_add(reference, xsec_token, content, content_file, dry_run):
        """在指定笔记下发表评论。"""
        feed_id, token = parse_reference(reference, xsec_token=xsec_token)
        text = content_text(content, content_file)
        payload = {"feed_id": feed_id, "xsec_token": token, "content": text}
        dispatch(
            "comment.add",
            "POST",
            "/api/v1/feeds/comment",
            payload,
            lambda: backend().comment(feed_id, token, text),
            dry_run,
        )

    @comment.command("reply")
    @reference_options
    @content_options
    @click.option("--comment-id", help="目标评论 ID；与 --user-id 至少提供一个。")
    @click.option("--user-id", help="目标用户 ID；优先使用精确的 --comment-id。")
    @dry_run_option
    def comment_reply(
        reference, xsec_token, content, content_file, comment_id, user_id, dry_run
    ):
        """回复指定评论或指定用户。"""
        feed_id, token = parse_reference(reference, xsec_token=xsec_token)
        text = content_text(content, content_file)
        if not comment_id and not user_id:
            raise click.UsageError("回复需指定 --comment-id 或 --user-id。")
        payload = {"feed_id": feed_id, "xsec_token": token, "content": text}
        if comment_id is not None:
            payload["comment_id"] = _id(comment_id, "comment-id")
        if user_id is not None:
            payload["user_id"] = _id(user_id, "user-id")
        dispatch(
            "comment.reply",
            "POST",
            "/api/v1/feeds/comment/reply",
            payload,
            lambda: backend().reply_comment(
                feed_id, token, text, comment_id=comment_id, user_id=user_id
            ),
            dry_run,
        )

    @cli.group()
    def notifications():
        """读取通知，回复或点赞通知中的评论。"""

    @notifications.command("unread")
    def notifications_unread():
        """读取评论和@、赞和收藏、新增关注的未读数。"""
        emit(backend().notifications_unread())

    @notifications.command("list")
    @click.option(
        "--tab",
        type=click.Choice(["mentions", "likes", "connections"]),
        default="mentions",
        show_default=True,
    )
    @click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
    def notifications_list(tab, limit):
        """读取选定分区通知（访问通知页可能改变已读状态）。"""
        emit(backend().notifications(tab=tab, limit=limit))

    @notifications.command("reply")
    @click.argument("comment_id")
    @content_options
    @dry_run_option
    def notifications_reply(comment_id, content, content_file, dry_run):
        """回复通知中的 comment_id（不是通知 id）。"""
        comment_id = _id(comment_id, "comment-id")
        text = content_text(content, content_file)
        dispatch(
            "notifications.reply",
            "POST",
            "/api/v1/notifications/reply",
            {"comment_id": comment_id, "content": text},
            lambda: backend().notification_reply(comment_id, text),
            dry_run,
        )

    def add_notification_like(name, unlike):
        @notifications.command(name)
        @click.argument("comment_id")
        @dry_run_option
        def action(comment_id, dry_run):
            comment_id = _id(comment_id, "comment-id")
            dispatch(
                f"notifications.{name}",
                "POST",
                "/api/v1/notifications/like",
                {"comment_id": comment_id, "unlike": unlike},
                lambda: backend().notification_like(comment_id, unlike=unlike),
                dry_run,
            )

        action.help = "取消通知评论点赞。" if unlike else "点赞通知中的评论。"

    add_notification_like("like", False)
    add_notification_like("unlike", True)
