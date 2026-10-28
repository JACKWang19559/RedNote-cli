---
name: cli-anything-rednote
description: 使用 RedNote CLI 操作小红书：登录、搜索与阅读、图文和视频发布、笔记点赞收藏、评论回复、通知中心及本地草稿。适用于已安装 rednote 或 cli-anything-rednote 的环境，在线功能需要 xiaohongshu-mcp 后端。
---

# RedNote CLI 0.2

使用 `rednote`，等价入口是 `cli-anything-rednote`。Python 3.10+，从项目仓库安装：`python -m pip install -e .`。
先用 `rednote --json info`、`rednote --json capabilities` 和 `rednote --help` 发现当前命令。

## 配置、输出与登录

全局选项必须放在子命令前：

```text
rednote --json --home PATH --backend-url URL --timeout 120 COMMAND
```

环境变量为 `REDNOTE_HOME`、`REDNOTE_BACKEND_URL`、`REDNOTE_TIMEOUT`。可选 Bearer 令牌只通过 `REDNOTE_BACKEND_TOKEN` 传入，`config show` 不显示令牌内容。默认后端根地址是 `http://127.0.0.1:18060`，不要附加 `/mcp` 或 `/api/v1`。在线命令依赖独立运行的 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) 服务，浏览器与登录由后端管理。

```text
rednote status
rednote auth qrcode --output login.png
rednote auth status
rednote auth logout --dry-run
```

二维码让用户用小红书 App 扫码，之后查询登录状态。输出文件不覆盖已有内容；已登录时不会生成二维码。`auth logout` 删除后端登录 Cookies，可加 `--dry-run` 预览。后端不可用时准确报告依赖问题，本地草稿仍可使用。

自动化使用 `--json`。成功 `{"ok":true,"data":...}`，失败 `{"ok":false,"error":{"code":"...","message":"...","hint":"..."}}`；`hint` 可省略。退出码：0 成功、1 业务或连接错误、2 用法错误、130 普通中断。写请求执行中 Ctrl+C 或传输失败且结果不明时，返回 `BACKEND_OUTCOME_UNKNOWN`、退出 1，不能认定操作取消。帮助/版本仍输出普通文本。保留后端数据结构，包括嵌套 `data`；不要把预览输出当作线上操作完成。

## 搜索与读取

```text
rednote --json search "杭州咖啡" --sort 最新 --type 图文 --time 一周内 --scope 未看过 --location 同城
rednote --json feed list
rednote --json note get "FULL_NOTE_URL"
rednote --json note get NOTE_ID --xsec-token TOKEN --all-comments --comment-limit 50 --expand-replies --reply-limit 20 --scroll-speed normal
rednote --json user get "FULL_PROFILE_URL" --tab note
rednote --json user get USER_ID --xsec-token TOKEN --tab liked
rednote --json user me --tab fav
```

示例大写值必须替换为真实输入。笔记/用户 ID 为 24 位十六进制字符串。完整 HTTPS 小红书链接包含 `xsec_token`，也可显式使用 `--xsec-token`。笔记路径支持 `/explore/`、`/discovery/item/`，主页支持 `/user/profile/`。短链接需先由浏览器打开后取得完整地址，整条 URL 用引号包裹。搜索结果的 `xsecToken` 对应笔记；主页要使用对应主页的 token，不能编造或随意互换。

筛选枚举：

| 选项 | 值 |
| --- | --- |
| `--sort` | 综合、最新、最多点赞、最多评论、最多收藏 |
| `--type` | 不限、图文、视频 |
| `--time` | 不限、一天内、一周内、半年内 |
| `--scope` | 不限、已看过、未看过、已关注 |
| `--location` | 不限、同城、附近 |
| 主页 `--tab` | `note`、`fav`、`liked` |
| 评论 `--scroll-speed` | `slow`、`normal`、`fast` |

高级评论选项自动开启更多评论加载，`--reply-limit` 需同时使用 `--expand-replies`。`--comment-limit` 是主评论加载目标；`--reply-limit` 是展开回复的阈值，超过它的回复组会跳过，不是返回数组的最大长度。当前后端把 0 还原为默认 20 条主评论、回复阈值 10，不承诺无限抓取。搜索、推荐与主页返回当前批次，没有自动分页。

## 发布与离线预览

```text
rednote --json publish image --title "咖啡探店" --content-file body.txt --image cover.jpg --image second.jpg --tag 咖啡 --visibility 公开可见 --dry-run
rednote --json publish video --title "周末记录" --content "正文" --video video.mp4 --tag 生活 --dry-run
```

发布需标题、非空正文与媒体，`--content`/`--content-file` 二选一，文件为 UTF-8（兼容 BOM）。图片 `--image` 可重复；视频只有一个 `--video` 文件。图文/视频支持重复 `--tag`、重复 `--product`、`--schedule-at`、`--visibility`、`--server-paths`、`--dry-run`；只有图文支持 `--original`。

发布标题使用上游加权规则：ASCII 1、其他 BMP 字符 2、非 BMP 字符 4，总分除以 2 向上取整不超过 20。规范化后最多 10 个不同标签。正文最终限制由发布页面检查。可见范围为 `公开可见`、`仅自己可见`、`仅互关好友可见`。时间需带时区 ISO8601，例如 `2026-10-30T10:00:00+08:00`；使用时根据当前时间重新计算，后端接受 1 小时后至 14 天内。商品参数是后端搜索条件，后端自动选择首个匹配项，不能保证精确匹配某一商品。

媒体由后端读取，CLI 不传输文件。普通路径在 CLI 所在机器检查并转为绝对路径；Docker/远程后端应先准备可读媒体，再使用对应绝对路径和 `--server-paths`。图片还支持 HTTP(S) URL，由后端下载；视频不支持 URL。`--server-paths` 不检查远端文件是否存在。

显式调用写命令会执行对应远程动作；可先用 `--dry-run` 做无网络参数校验和请求预览。只有去掉 `--dry-run` 才发送请求。不要把批量或重复发布当作读取任务的一部分。CLI 不自动重试写入；超时、断连或执行中 Ctrl+C 后结果可能不明，先核对实际平台状态再决定是否重试。

## 笔记互动与评论

```text
rednote --json note like NOTE_ID --xsec-token TOKEN --dry-run
rednote --json note unlike NOTE_ID --xsec-token TOKEN --dry-run
rednote --json note favorite NOTE_ID --xsec-token TOKEN --dry-run
rednote --json note unfavorite NOTE_ID --xsec-token TOKEN --dry-run
rednote --json comment add NOTE_ID --xsec-token TOKEN --content "评论正文" --dry-run
rednote --json comment reply NOTE_ID --xsec-token TOKEN --comment-id COMMENT_ID --content-file reply.txt --dry-run
```

笔记参数也可用完整 URL。评论回复必须提供 `--comment-id` 或 `--user-id` 至少一个，使用实际评论结果中的值。正文也可用 `--content`。执行用户已要求的操作时去掉 `--dry-run`，无需额外 `--yes` 参数。

## 通知中心

```text
rednote --json notifications unread
rednote --json notifications list --tab mentions --limit 20
rednote --json notifications list --tab likes --limit 20
rednote --json notifications list --tab connections --limit 20
rednote --json notifications reply COMMENT_ID --content "回复正文" --dry-run
rednote --json notifications like COMMENT_ID --dry-run
rednote --json notifications unlike COMMENT_ID --dry-run
```

`mentions` 是评论和 @，`likes` 是赞和收藏，`connections` 是新增关注。`--limit` 为正整数；已删除或不可见条目会被过滤。回复/点赞使用条目的 `comment_id`，不能误用通知 `id`。回复也接受 `--content-file`。通知列表走实际页面，可能改变页面的已读状态。

## 本地草稿

```text
rednote --json draft create --title "咖啡探店" --content "正文" --tag 咖啡 --image cover.jpg
rednote --json draft list
rednote --json draft show DRAFT_ID
rednote --json draft update DRAFT_ID --content-file body.txt --image "C:\photos\coffee.jpg"
rednote --json draft update DRAFT_ID --clear-tags
rednote --json draft undo
rednote --json draft redo
rednote --json draft export DRAFT_ID --format markdown --output draft.md
rednote --json draft export DRAFT_ID --format json --output draft.json
rednote --json draft publish DRAFT_ID --visibility 仅自己可见 --dry-run
rednote --json draft delete DRAFT_ID
```

ID 来自 create/list。SQLite 默认位于 `~/.rednote/drafts.sqlite3`，全局 `--home` 或 `REDNOTE_HOME` 可隔离目录。修改与历史持久保存；撤销针对整个目录，新的修改清空重做分支。本地草稿标题最多 20 个 Unicode 字符、正文最多 1000 字符，与发布时加权标题校验不同。更新的重复 `--tag`/`--image` 替换对应列表，`--clear-tags`/`--clear-images` 清空。创建/更新时本地相对图片路径转为当时目录下的绝对路径，不检查文件存在性；HTTP(S) URL 和后端绝对路径保留。图片只保存引用，导出不覆盖文件、不嵌入媒体。

`draft publish` 默认把本地标题、正文、标签和图片提交为图文；`--video PATH` 改发视频并忽略图片引用。支持定时、可见范围、商品及后端路径选项，原创只支持图文。旧草稿若仍有相对图片引用，图文发布会拒绝；从正确目录显式 `draft update DRAFT_ID --image ...` 修正，或提供确定的后端绝对路径。视频覆盖时不使用这些旧图片。发布不删除本地草稿，也不自动同步到创作中心。`draft undo/redo` 无法撤销远程发布或互动。

## REPL 与范围

无参数进入 REPL；`rednote --json` 单独运行返回信息。`rednote --json repl` 每行执行一条命令，逐行 JSON（显式 help/version 除外）；任一命令失败使批处理最终退出非零。`help` 查帮助，`quit` 退出。引号组合参数，Windows 反斜杠保持字面值，历史只在内存。

`capabilities` 是 CLI 已实现能力清单，不探测当前后端版本。后端没有关注/取消关注、私信、编辑/删除已发布笔记、删除评论、运营分析、直播和店铺管理接口；不要构造不存在的命令，也不要把通知分栏或商品绑定描述成这些功能。线上成功必须有实际后端返回支持，离线和模拟测试不证明远程发布已完成。
