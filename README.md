# RedNote CLI 0.2

基于 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 方法构建的小红书命令行工具，覆盖当前 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) 后端开放的业务 REST 功能：登录、搜索、读取、图文/视频发布、评论回复、点赞收藏及通知中心。同时提供本地草稿、JSON 输出和交互式 REPL。

CLI-Anything 提供架构与交互规范；在线操作由独立运行的 xiaohongshu-mcp 浏览器后端执行。本仓库根目录就是独立的 agent harness。

## 安装与登录

先获取源码：

```bash
git clone https://github.com/JACKWang19559/RedNote-cli.git
cd RedNote-cli
```

需要 Python 3.10+。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\rednote.exe --help
```

激活虚拟环境后可直接使用 `rednote`；不激活时，把下文中的 `rednote` 替换为 `.\.venv\Scripts\rednote.exe`。`cli-anything-rednote` 是等价入口，也支持 `python -m cli_anything.rednote`。普通安装可以省略 `[dev]`。

macOS/Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

在线命令需要另行安装后端。按 [上游安装说明](https://github.com/xpzouying/xiaohongshu-mcp#1-使用教程) 操作，或从 [Releases](https://github.com/xpzouying/xiaohongshu-mcp/releases) 获取适合系统的可执行文件。Windows 示例：

```powershell
# 在另一个终端保持运行；首次启动可能下载浏览器
.\xiaohongshu-mcp-windows-amd64.exe

# 回到 CLI 终端
rednote status
rednote auth qrcode --output login.png
# 打开图片，用小红书 App 扫码
rednote auth status
rednote --json search "杭州咖啡"
```

二维码过期后换一个文件名重新获取；输出不覆盖已有文件。已经登录时只返回登录状态。`rednote auth logout` 删除后端保存的登录 Cookies，下次在线操作需要重新登录；可用 `--dry-run` 查看请求。

默认后端地址为 `http://127.0.0.1:18060`，应传服务根地址，不加 `/mcp` 或 `/api/v1`：

```powershell
$env:REDNOTE_BACKEND_URL = "http://127.0.0.1:18060"
$env:REDNOTE_TIMEOUT = "120"
# 后端启用 AUTH_TOKEN 时，设置相同值
$env:REDNOTE_BACKEND_TOKEN = "your-backend-token"
rednote config show
```

全局 `--backend-url`、`--timeout` 可覆盖环境变量。鉴权令牌不会出现在配置输出中；CLI 不读取浏览器 Cookies，也不使用系统 HTTP 代理。浏览器访问小红书所需的代理由后端配置。

## 命令范围

| 功能 | 命令 |
| --- | --- |
| 配置与发现 | `info`、`capabilities`、`config show`、`status` |
| 登录 | `auth status/qrcode/logout` |
| 内容读取 | `search`、`feed list`、`note get`、`user get/me` |
| 发布 | `publish image/video`、`draft publish` |
| 笔记互动 | `note like/unlike/favorite/unfavorite` |
| 评论 | `comment add/reply` |
| 通知 | `notifications unread/list/reply/like/unlike` |
| 本地草稿 | `draft create/list/show/update/delete/undo/redo/export` |

`rednote --json capabilities` 列出实现范围与后端未提供的能力；这不代表已探测当前部署的后端版本。关注/取消关注、私信、修改/删除已发布笔记、删除评论、运营分析、直播和店铺管理没有对应后端 API，本工具不提供这些命令。通知中的 `connections` 是新增关注通知。较旧后端可能缺少通知或主页分栏接口，需要升级后端。

## 搜索、笔记与主页

```powershell
rednote --json search "露营" --sort 最多收藏 --type 图文 --time 一周内 --scope 未看过 --location 同城
rednote --json feed list
rednote --json note get "https://www.xiaohongshu.com/explore/NOTE_ID?xsec_token=TOKEN"
rednote --json note get NOTE_ID --xsec-token TOKEN --all-comments --comment-limit 50 --expand-replies --reply-limit 20 --scroll-speed normal
rednote --json user get "https://www.xiaohongshu.com/user/profile/USER_ID?xsec_token=TOKEN" --tab note
rednote --json user me --tab fav
```

把示例中的 ID 和 TOKEN 替换成实际结果：笔记和用户 ID 是 24 位十六进制字符串，搜索结果中的 `xsecToken` 可配合 `--xsec-token`。主页需要对应主页的 token。完整 URL 必须加引号，支持 `/explore/`、`/discovery/item/`、`/user/profile/`；短链接应先在浏览器打开，再复制完整地址。

| 选项 | 可选值 |
| --- | --- |
| `search --sort` | 综合、最新、最多点赞、最多评论、最多收藏 |
| `search --type` | 不限、图文、视频 |
| `search --time` | 不限、一天内、一周内、半年内 |
| `search --scope` | 不限、已看过、未看过、已关注 |
| `search --location` | 不限、同城、附近 |
| `user get/me --tab` | `note` 笔记、`fav` 收藏、`liked` 点赞 |
| `note get --scroll-speed` | `slow`、`normal`、`fast` |

指定高级评论选项会自动开启更多评论加载；`--reply-limit` 必须同时指定 `--expand-replies`。`--comment-limit` 是主评论加载目标；`--reply-limit` 是展开回复的数量阈值，超过阈值的回复组会被跳过，并非把返回的回复裁剪为 N 条。当前后端把 0 处理为默认值：20 条主评论、回复阈值 10；`--all-comments` 不承诺无限加载。搜索、推荐和主页返回后端当前批次，没有自动翻页；结果受账号可见范围限制。

## 图文与视频发布

```powershell
# 图文：--image 可重复，也可使用后端能够下载的图片 URL
rednote --timeout 300 publish image --title "周末探店" --content-file .\正文.txt --image "D:\照片\咖啡.jpg" --tag 咖啡 --visibility 公开可见

# 视频：一个后端可读的本地视频文件
rednote --timeout 600 publish video --title "周末记录" --content "记录这一天。" --video "D:\视频\周末.mp4" --tag 生活

# 不联网，校验参数并查看将提交的请求
rednote --json publish image --title "预览" --content "正文" --image "D:\照片\咖啡.jpg" --dry-run

# 定时：带时区的 ISO8601 时间，当前后端允许 1 小时后至 14 天内
$publishAt = (Get-Date).AddHours(2).ToString("yyyy-MM-ddTHH:mm:sszzz")
rednote publish image --title "稍后发布" --content-file .\正文.txt --image .\封面.jpg --schedule-at $publishAt --original
```

标题、非空正文和媒体为必填；`--content` 与 `--content-file` 二选一。正文文件为 UTF-8，兼容 BOM。发布标题按上游加权规则校验：ASCII 计 1、其他 BMP 字符计 2、非 BMP 字符计 4，总数除以 2 向上取整不能超过 20；例如纯中文最多 20 字、纯 ASCII 最多 40 字。正文最终限制由发布页面校验。

图文/视频均支持重复 `--tag`（规范化后最多 10 个）、重复 `--product`、`--schedule-at` 和 `--visibility`。可见范围为 `公开可见`、`仅自己可见`、`仅互关好友可见`。`--original` 仅用于图文。`--product` 把商品搜索词交给后端，自动选择首个匹配项；不保证关键词对应某个确定商品。

**媒体路径由后端读取，CLI 不传输文件内容。** 同一台机器时，CLI 检查本地文件并转成绝对路径。后端在 Docker 或另一台机器时，先把文件放入后端可读的位置，再传后端路径并加 `--server-paths`：

```powershell
rednote publish image --title "远程媒体" --content "正文" --image /app/images/cover.jpg --server-paths --dry-run
```

图片支持 HTTP(S) URL；视频仅支持文件路径。`--server-paths` 不会上传文件，也不验证远端文件是否存在。

## 点赞、收藏与评论

```powershell
rednote note like NOTE_ID --xsec-token TOKEN
rednote note unlike NOTE_ID --xsec-token TOKEN
rednote note favorite NOTE_ID --xsec-token TOKEN
rednote note unfavorite NOTE_ID --xsec-token TOKEN
rednote comment add NOTE_ID --xsec-token TOKEN --content "这家店的地址在哪里？"
rednote comment reply NOTE_ID --xsec-token TOKEN --comment-id COMMENT_ID --content-file .\回复.txt
```

以上笔记参数也接受含 token 的完整链接。`comment reply` 至少提供 `--comment-id` 或 `--user-id`，值取自真实评论数据。所有发布、互动、回复及退出登录命令直接执行指定操作；加 `--dry-run` 可离线校验并查看请求。CLI 不自动重试写操作；写请求超时、断连或执行中按 Ctrl+C 后可能无法确认结果，应先核对平台实际状态，再决定是否重试。

## 通知中心

```powershell
rednote --json notifications unread
rednote --json notifications list --tab mentions --limit 30
rednote --json notifications list --tab likes --limit 20
rednote --json notifications list --tab connections --limit 20
rednote notifications reply COMMENT_ID --content "谢谢分享！"
rednote notifications like COMMENT_ID
rednote notifications unlike COMMENT_ID
```

分栏为 `mentions` 评论和 @、`likes` 赞和收藏、`connections` 新增关注。`--limit` 为正整数，后端可能因内容删除或不可见返回更少条目。回复和点赞使用通知条目的 `comment_id`，不是通知的 `id`；可以加 `--dry-run`，回复也支持 `--content-file`。读取列表依赖实际通知页面，可能影响页面的已读状态。

## 本地草稿与发布

```powershell
rednote draft create --title "周末咖啡探店" --content "记录两家喜欢的小店。" --tag 咖啡 --image "D:\照片\咖啡.jpg"
rednote draft list
# DRAFT_ID 使用 create/list 返回的 id
rednote draft update DRAFT_ID --content-file .\正文.txt
rednote draft show DRAFT_ID
rednote draft undo
rednote draft redo
rednote draft export DRAFT_ID --format markdown --output .\草稿.md
rednote draft export DRAFT_ID --format json --output .\草稿.json
rednote draft publish DRAFT_ID --dry-run
rednote --timeout 300 draft publish DRAFT_ID --visibility 仅自己可见
rednote --timeout 600 draft publish DRAFT_ID --video "D:\视频\周末.mp4" --dry-run
rednote draft delete DRAFT_ID
rednote draft undo
```

草稿存于 `~/.rednote/drafts.sqlite3`，用全局 `--home PATH` 或 `REDNOTE_HOME` 隔离不同工作目录。修改立即保存，撤销/重做跨进程有效；新编辑清空重做分支。历史针对整个 home。`draft undo/redo` 只撤销本地修改，不能撤销线上发布、评论、点赞或收藏。

草稿标题最多 20 个 Unicode 字符、正文最多 1000 字符，使用独立的本地编辑校验。重复 `--tag`/`--image` 在更新时替换整个列表；`--clear-tags`/`--clear-images` 清空。创建或更新时，本地相对图片路径按当时的工作目录转为绝对路径，不要求文件已经存在；HTTP(S) URL 和绝对后端路径保留。图片只保存引用，导出不复制媒体且不覆盖已有文件。

`draft publish` 默认读取本地标题、正文、标签和图片提交图文；加 `--video PATH` 改发视频并忽略草稿中的图片。支持定时、可见范围、商品及后端路径选项，`--original` 仅用于图文。历史草稿若仍有相对图片路径，图文发布会拒绝：请从正确目录用 `draft update DRAFT_ID --image ...` 重新写入图片，或指定明确的后端绝对路径。草稿不会自动同步到创作中心，也不会因发布而自动删除。

## JSON、REPL 与 Agent

全局参数放在子命令前：

```powershell
rednote --json --home .\workspace-drafts draft list
rednote --json --timeout 120 search "旅行"
rednote --json capabilities
```

成功为 `{"ok":true,"data":...}`，失败为 `{"ok":false,"error":{"code":"...","message":"...","hint":"..."}}`，`hint` 可省略。JSON 写入 stdout；退出码成功 0、业务/连接错误 1、用法错误 2、普通中断 130。远程写入执行中断或传输失败且结果无法确认时，返回 `BACKEND_OUTCOME_UNKNOWN`、退出 1，不表示操作已取消。`--help` 和 `--version` 使用文本。`data` 保留后端字段，包括可能存在的嵌套 `data`；请求预览不代表线上操作成功。

`rednote` 或 `rednote repl` 进入交互模式，`help`/`help draft` 查看帮助，`quit` 退出。引号包含带空格的参数，Windows 反斜杠保持字面值，命令历史只存在内存中。`rednote --json repl` 逐行执行并输出 JSON，任一命令失败使最终退出码非零；`rednote --json` 单独运行返回信息。`rednote info` 返回安装的 Agent skill 路径，仓库副本在 [skills/cli-anything-rednote/SKILL.md](skills/cli-anything-rednote/SKILL.md)。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check cli_anything tests live
.\.venv\Scripts\python.exe -m build
$env:CLI_ANYTHING_FORCE_INSTALLED = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_full_e2e.py -v

# 需要真实后端和登录；独立执行只读在线验证
.\.venv\Scripts\python.exe -m pytest live -v
```

默认测试验证实际本地草稿文件、HTTP 契约模拟、命令参数与离线请求预览，不访问账号。`live` 实际调用后端，服务不可用或未登录时失败。真实笔记/主页链接可通过 `REDNOTE_LIVE_NOTE_URL`、`REDNOTE_LIVE_USER_URL` 提供。本次没有执行线上发布或互动测试；结果与范围见 [tests/TEST.md](tests/TEST.md)。

架构与逐项路由映射见 [REDNOTE.md](REDNOTE.md)。接口核对依据为上游 [routes.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/routes.go)、[types.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/types.go)、[service.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/service.go) 与业务实现。REPL 外观派生自 CLI-Anything，见 [NOTICE](NOTICE)。本项目为独立社区工具，采用 Apache-2.0 许可证。
