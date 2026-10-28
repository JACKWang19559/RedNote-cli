# RedNote CLI 0.2

基于 CLI-Anything 的小红书命令行工具，支持当前 xiaohongshu-mcp 后端的登录、搜索、阅读、图文/视频发布、评论、点赞收藏和通知中心，并提供本地草稿、JSON 与 REPL。

## 安装与连接

Python 3.10+，在仓库根目录运行 `python -m pip install -e .`。
使用 `rednote --help` 或等价入口 `cli-anything-rednote`；`rednote --json capabilities` 查看能力范围。

在线操作需要独立运行的 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)。默认根地址为 `http://127.0.0.1:18060`；用 `REDNOTE_BACKEND_URL` 或全局 `--backend-url` 修改，不加 `/mcp`。可选 Bearer 鉴权只通过 `REDNOTE_BACKEND_TOKEN` 配置。

```text
rednote status
rednote auth qrcode --output login.png
rednote auth status
rednote --json search "杭州咖啡" --scope 未看过
rednote --json note get "FULL_NOTE_URL_WITH_XSEC_TOKEN"
rednote --json user me --tab fav
rednote --json notifications list --tab mentions --limit 20
```

## 发布、互动与草稿

```text
rednote publish image --title "探店" --content-file body.txt --image cover.jpg --tag 咖啡 --dry-run
rednote publish video --title "周末" --content "正文" --video video.mp4 --dry-run
rednote note like NOTE_ID --xsec-token TOKEN --dry-run
rednote comment reply NOTE_ID --xsec-token TOKEN --comment-id COMMENT_ID --content "回复正文" --dry-run
rednote draft create --title "草稿" --content "正文" --image cover.jpg
rednote draft publish DRAFT_ID --dry-run
```

去掉 `--dry-run` 即执行远程写入；预览不联网。媒体由后端读取，CLI 不传输文件。远程或容器路径使用 `--server-paths`，视频只支持文件路径，图片另支持 URL。完整参数、定时与可见范围见各命令 `--help`。

本地草稿位于 `~/.rednote/drafts.sqlite3`，可用全局 `--home` 或 `REDNOTE_HOME` 隔离。草稿支持编辑、导出、删除及跨进程撤销/重做。创建/更新时，相对图片路径转为当时目录下的绝对路径，URL 和后端绝对路径保留，不检查文件存在性。旧草稿中的相对图片路径须从正确目录用 `draft update DRAFT_ID --image ...` 修正后才能发布图文。`draft publish --video PATH` 改发视频并忽略图片；本地撤销不影响远程操作。

`--json` 放在子命令前，输出 `{ok,data}` 或 `{ok:false,error}`；失败退出非零。写请求执行中按 Ctrl+C 或发生传输失败且结果无法确认时，返回 `BACKEND_OUTCOME_UNKNOWN`、退出 1，应先核对平台状态。`rednote` 进入 REPL，`rednote --json repl` 可逐行执行。Agent 说明位于本文件旁的 `skills/SKILL.md`，可用 `rednote info` 获取路径。

当前后端不支持关注/取消关注、私信、编辑/删除已发布笔记、删除评论、运营分析、直播和店铺管理，也不支持自动分页。本工具的能力范围是后端已开放接口，不是整个平台。

## 验证

安装开发依赖 `python -m pip install -e '.[dev]'` 后运行 `python -m pytest tests`。
默认测试不访问账号。`python -m pytest live -v` 独立验证已登录的真实后端；服务不可用或未登录会失败。远程写操作未在线执行验证。
