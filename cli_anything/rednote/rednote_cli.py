"""Click commands shared by one-shot use and the interactive REPL."""

from __future__ import annotations

import base64
import binascii
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from . import __version__
from .core.backend import Backend
from .core.capabilities import describe_capabilities
from .core.draft_media import bind_draft_images
from .core.drafts import DraftStore
from .core.errors import RedNoteError
from .core.refs import parse_reference
from .remote_commands import register_remote_commands


@dataclass
class Settings:
    home: Path
    backend_url: str
    timeout: float
    json_output: bool = False
    in_repl: bool = False


def _error(error: RedNoteError, json_output: bool) -> None:
    if json_output:
        click.echo(
            json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False)
        )
    else:
        click.echo(f"错误 [{error.code}]: {error.message}", err=True)
        if error.hint:
            click.echo(f"提示: {error.hint}", err=True)


class RootGroup(click.Group):
    def main(
        self,
        args=None,
        prog_name=None,
        complete_var=None,
        standalone_mode=True,
        **extra,
    ):
        args = list(sys.argv[1:] if args is None else args)
        options = args[: args.index("--")] if "--" in args else args
        json_output = "--json" in options
        code = 0
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
            if isinstance(result, int):
                code = result
        except RedNoteError as exc:
            _error(exc, json_output)
            code = 1
        except click.ClickException as exc:
            _error(
                RedNoteError(
                    "USAGE_ERROR",
                    exc.format_message(),
                    "运行 rednote --help 查看用法。",
                ),
                json_output,
            )
            code = exc.exit_code
        except (OSError, UnicodeError):
            _error(
                RedNoteError(
                    "FILE_ERROR", "无法读写文件，请检查路径、权限及 UTF-8 编码。"
                ),
                json_output,
            )
            code = 1
        except (click.Abort, KeyboardInterrupt):
            _error(RedNoteError("INTERRUPTED", "操作已取消。"), json_output)
            code = 130
        if standalone_mode:
            raise SystemExit(code)
        return code


def emit(data, settings: Settings | None = None) -> None:
    settings = settings or click.get_current_context().obj
    if settings.json_output:
        click.echo(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    else:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def backend() -> Backend:
    settings = click.get_current_context().obj
    client = Backend(
        settings.backend_url,
        timeout=settings.timeout,
        token=os.getenv("REDNOTE_BACKEND_TOKEN") or None,
    )
    click.get_current_context().call_on_close(client.close)
    return client


def drafts() -> DraftStore:
    return DraftStore(click.get_current_context().obj.home)


def _read_content(content: str | None, content_file: Path | None) -> str | None:
    if content is not None and content_file is not None:
        raise click.UsageError("--content 与 --content-file 不能同时使用。")
    if content_file is not None:
        return content_file.read_text(encoding="utf-8-sig")
    return content


@click.group(
    cls=RootGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--json", "json_output", is_flag=True, help="输出单个 JSON 对象；放在子命令前。"
)
@click.option(
    "--home",
    type=click.Path(path_type=Path, file_okay=False),
    envvar="REDNOTE_HOME",
    default=None,
    help="本地草稿目录，默认 ~/.rednote。",
)
@click.option(
    "--backend-url",
    envvar="REDNOTE_BACKEND_URL",
    default="http://127.0.0.1:18060",
    show_default=True,
    help="xiaohongshu-mcp 服务根地址（不是 /mcp）。",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0.1, max=600),
    envvar="REDNOTE_TIMEOUT",
    default=60.0,
    show_default=True,
    help="HTTP 超时秒数。",
)
@click.version_option(__version__)
@click.pass_context
def cli(ctx, json_output, home, backend_url, timeout):
    """小红书 CLI：搜索、发布、互动、通知与本地草稿。无参数进入交互模式。"""
    in_repl = isinstance(ctx.obj, Settings) and ctx.obj.in_repl
    ctx.obj = Settings(
        (home or Path.home() / ".rednote").expanduser().resolve(),
        backend_url,
        timeout,
        json_output,
        in_repl,
    )
    if ctx.invoked_subcommand is None:
        if json_output:
            ctx.invoke(info)
        else:
            ctx.invoke(repl)


@cli.command()
@click.pass_context
def info(ctx):
    """显示版本、可用命令与随包提供的 Agent skill 路径。"""
    emit(
        {
            "name": "cli-anything-rednote",
            "version": __version__,
            "commands": sorted(cli.commands),
            "home": str(ctx.obj.home),
            "skill_path": str(Path(__file__).parent / "skills" / "SKILL.md"),
            "backend": "xiaohongshu-mcp REST API",
            "remote_operations": "read and write; write commands support --dry-run",
            "drafts": "local SQLite; not synced to Xiaohongshu",
        }
    )


@cli.command()
def capabilities():
    """列出后端接口覆盖范围和暂不支持的平台功能（离线）。"""
    emit(describe_capabilities())


@cli.command()
def status():
    """检查真实后端健康状态和小红书登录状态。"""
    client = backend()
    emit({"health": client.health(), "login": client.login_status()})


@cli.group()
def config():
    """查看配置（通过全局选项或环境变量设置）。"""


@config.command("show")
@click.pass_context
def config_show(ctx):
    """显示生效配置，凭证只显示是否已设置。"""
    # Let Backend validate the URL before echoing it, so embedded credentials are never printed.
    backend()
    emit(
        {
            "home": str(ctx.obj.home),
            "backend_url": ctx.obj.backend_url,
            "timeout": ctx.obj.timeout,
            "token_configured": bool(os.getenv("REDNOTE_BACKEND_TOKEN")),
        }
    )


@cli.group()
def auth():
    """登录状态与扫码登录二维码。"""


@auth.command("status")
def auth_status():
    """查询登录状态。"""
    emit(backend().login_status())


@auth.command("qrcode")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="保存 PNG 二维码，不覆盖现有文件。",
)
def auth_qrcode(output_path):
    """获取登录二维码；扫码后用 auth status 检查。"""
    data = backend().login_qrcode()
    if output_path is not None:
        if not isinstance(data, dict):
            raise RedNoteError("INVALID_RESPONSE", "后端未返回二维码对象。")
        if data.get("is_logged_in") is True:
            emit(data)
            return
        encoded = data.get("img", "")
        if not isinstance(encoded, str):
            raise RedNoteError("INVALID_RESPONSE", "后端二维码格式无效。")
        if encoded.startswith("data:image/png;base64,"):
            encoded = encoded.split(",", 1)[1]
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise RedNoteError(
                "INVALID_RESPONSE", "后端二维码不是有效的 Base64 PNG。"
            ) from None
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RedNoteError("INVALID_RESPONSE", "后端二维码不是 PNG 文件。")
        try:
            with output_path.open("xb") as handle:
                handle.write(image)
        except FileExistsError:
            raise RedNoteError(
                "FILE_EXISTS", "二维码输出文件已存在，请指定新路径。"
            ) from None
        data = {key: value for key, value in data.items() if key != "img"}
        data["path"] = str(output_path.resolve())
    emit(data)


@cli.command()
@click.argument("keyword")
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["综合", "最新", "最多点赞", "最多评论", "最多收藏"]),
    default="综合",
)
@click.option(
    "--type", "note_type", type=click.Choice(["不限", "图文", "视频"]), default="不限"
)
@click.option(
    "--time",
    "publish_time",
    type=click.Choice(["不限", "一天内", "一周内", "半年内"]),
    default="不限",
)
@click.option(
    "--scope",
    "search_scope",
    type=click.Choice(["不限", "已看过", "未看过", "已关注"]),
    default=None,
)
@click.option("--location", type=click.Choice(["不限", "同城", "附近"]), default=None)
def search(keyword, sort_by, note_type, publish_time, search_scope, location):
    """按关键词搜索笔记；结果包含 ID 和 xsecToken。"""
    if not keyword.strip():
        raise click.UsageError("搜索关键词不能为空。")
    extra = {}
    if search_scope is not None:
        extra["search_scope"] = search_scope
    if location is not None:
        extra["location"] = location
    emit(
        backend().search(
            keyword,
            sort_by=sort_by,
            note_type=note_type,
            publish_time=publish_time,
            **extra,
        )
    )


@cli.group()
def feed():
    """首页推荐内容。"""


@feed.command("list")
def feed_list():
    """获取后端当前返回的一批首页推荐。"""
    emit(backend().feeds())


@cli.group()
def note():
    """读取详情、点赞和收藏笔记。"""


@note.command("get")
@click.argument("reference")
@click.option("--xsec-token", help="使用裸笔记 ID 时必须提供；完整链接可自动提取。")
@click.option(
    "--all-comments", is_flag=True, help="请求后端进一步加载评论，实际数量由后端决定。"
)
@click.option(
    "--comment-limit",
    type=click.IntRange(min=0),
    help="一级评论加载数；0 使用后端默认值。自动开启更多评论加载。",
)
@click.option(
    "--expand-replies", is_flag=True, help="展开二级回复；自动开启更多评论加载。"
)
@click.option(
    "--reply-limit",
    type=click.IntRange(min=0),
    help="跳过回复数量超过阈值的展开按钮；需 --expand-replies。0 使用后端默认。",
)
@click.option(
    "--scroll-speed", type=click.Choice(["slow", "normal", "fast"]), default=None
)
def note_get(
    reference,
    xsec_token,
    all_comments,
    comment_limit,
    expand_replies,
    reply_limit,
    scroll_speed,
):
    """REFERENCE 为含 xsec_token 的完整笔记 URL 或笔记 ID。"""
    feed_id, token = parse_reference(reference, kind="note", xsec_token=xsec_token)
    if reply_limit is not None and not expand_replies:
        raise click.UsageError("--reply-limit 需要同时指定 --expand-replies。")
    extra = {}
    if (
        comment_limit is not None
        or expand_replies
        or reply_limit is not None
        or scroll_speed is not None
    ):
        extra["comment_config"] = {
            "max_comment_items": comment_limit if comment_limit is not None else 20,
            "click_more_replies": expand_replies,
            "max_replies_threshold": reply_limit if reply_limit is not None else 10,
            "scroll_speed": scroll_speed or "normal",
        }
        all_comments = True
    emit(backend().note(feed_id, token, load_all_comments=all_comments, **extra))


@cli.group()
def user():
    """读取用户主页。"""


@user.command("get")
@click.argument("reference")
@click.option("--xsec-token", help="裸用户 ID 对应的主页 xsec_token。")
@click.option(
    "--tab",
    type=click.Choice(["note", "fav", "liked"]),
    default=None,
    help="笔记、收藏或点赞；后两项受主页隐私设置限制。",
)
def user_get(reference, xsec_token, tab):
    """REFERENCE 为完整用户主页 URL 或用户 ID。"""
    user_id, token = parse_reference(reference, kind="user", xsec_token=xsec_token)
    emit(backend().profile(user_id, token, **({"tab": tab} if tab is not None else {})))


@user.command("me")
@click.option("--tab", type=click.Choice(["note", "fav", "liked"]), default=None)
def user_me(tab):
    """读取当前登录用户主页。"""
    emit(backend().me(**({"tab": tab} if tab is not None else {})))


@cli.group()
def draft():
    """本地草稿管理；draft publish 可显式发布。"""


def content_options(function):
    function = click.option("--content", help="正文文本。")(function)
    function = click.option(
        "--content-file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="从 UTF-8 文件读取正文。",
    )(function)
    function = click.option("--tag", "tags", multiple=True, help="话题标签，可重复。")(
        function
    )
    return click.option(
        "--image", "images", multiple=True, help="图片路径或 URL，仅保存引用，可重复。"
    )(function)


@draft.command("create")
@click.option("--title", required=True, help="草稿标题，最多 20 个 Unicode 字符。")
@content_options
def draft_create(title, content, content_file, tags, images):
    """创建草稿并立即保存到本地。"""
    emit(
        drafts().create(
            title,
            _read_content(content, content_file) or "",
            tags=list(tags),
            images=bind_draft_images(images),
        )
    )


@draft.command("list")
def draft_list():
    """列出全部本地草稿。"""
    emit(drafts().list())


@draft.command("show")
@click.argument("draft_id")
def draft_show(draft_id):
    """查看草稿内容。"""
    emit(drafts().get(draft_id))


@draft.command("update")
@click.argument("draft_id")
@click.option("--title", default=None)
@content_options
@click.option("--clear-tags", is_flag=True)
@click.option("--clear-images", is_flag=True)
def draft_update(
    draft_id, title, content, content_file, tags, images, clear_tags, clear_images
):
    """更新指定字段；--tag/--image 替换对应列表。"""
    if (tags and clear_tags) or (images and clear_images):
        raise click.UsageError("清空选项不能和对应的 --tag/--image 同时使用。")
    content = _read_content(content, content_file)
    if (
        title is None
        and content is None
        and not (tags or images or clear_tags or clear_images)
    ):
        raise click.UsageError("请至少指定一个待修改字段。")
    emit(
        drafts().update(
            draft_id,
            title=title,
            content=content,
            tags=[] if clear_tags else list(tags) if tags else None,
            images=[]
            if clear_images
            else bind_draft_images(images)
            if images
            else None,
        )
    )


@draft.command("delete")
@click.argument("draft_id")
def draft_delete(draft_id):
    """删除本地草稿；可用 draft undo 撤销。"""
    emit(drafts().delete(draft_id))


@draft.command("undo")
def draft_undo():
    """撤销当前 home 下最近一次草稿修改。"""
    emit(drafts().undo())


@draft.command("redo")
def draft_redo():
    """重做最近撤销的草稿修改。"""
    emit(drafts().redo())


@draft.command("export")
@click.argument("draft_id")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    show_default=True,
)
def draft_export(draft_id, output_path, format_):
    """导出 UTF-8 草稿文件，不覆盖已存在的文件。"""
    emit(drafts().export(draft_id, output_path, format=format_))


def split_repl_line(line: str) -> list[str]:
    """Quotes group words; backslashes stay literal for Windows paths."""
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


@cli.command()
@click.pass_context
def repl(ctx):
    """交互式命令行；help 查看帮助，quit 退出。管道输入支持逐行执行。"""
    settings = ctx.obj
    if settings.in_repl:
        raise RedNoteError("USAGE_ERROR", "已经在 REPL 中。")
    settings.in_repl = True
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    skin = session = None
    if interactive and not settings.json_output:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory

        from .utils.repl_skin import ReplSkin

        skin = ReplSkin(
            "rednote", version=__version__, history_file=str(settings.home / "history")
        )
        skin.print_banner()
        session = PromptSession(
            history=InMemoryHistory(), style=skin.get_prompt_style()
        )
    prefix = [
        "--home",
        str(settings.home),
        "--backend-url",
        settings.backend_url,
        "--timeout",
        str(settings.timeout),
    ]
    if settings.json_output:
        prefix.append("--json")
    failed = False
    while True:
        try:
            line = skin.get_input(session) if skin else sys.stdin.readline()
            if not skin and line == "":
                break
            args = split_repl_line(line.strip())
            if not args:
                continue
            if args[0] in {"quit", "exit"}:
                break
            if args[0] == "repl":
                raise RedNoteError("USAGE_ERROR", "已经在 REPL 中。")
            if args[0] == "help":
                args = ["info"] if settings.json_output else args[1:] + ["--help"]
            code = cli.main(
                prefix + args, prog_name="rednote", standalone_mode=False, obj=settings
            )
            failed = failed or code != 0
        except EOFError:
            break
        except KeyboardInterrupt:
            if not interactive:
                ctx.exit(130)
            continue
        except (ValueError, RedNoteError) as exc:
            error = (
                exc
                if isinstance(exc, RedNoteError)
                else RedNoteError("USAGE_ERROR", "引号未闭合。")
            )
            _error(error, settings.json_output)
            failed = True
    if skin:
        skin.print_goodbye()
    if failed and not interactive:
        ctx.exit(1)


register_remote_commands(cli, auth, note, draft, backend, drafts, emit, _read_content)


def main():
    # Force UTF-8 for piped JSON on Windows as well as for Chinese terminals.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    cli()


if __name__ == "__main__":
    main()
