# RedNote 0.2：后端能力与 Harness 设计

本项目把 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) 当前开放的业务 REST API 映射为命令。在线动作由真实浏览器后端执行；CLI 提供参数校验、配置、输出、离线请求预览与本地草稿。接口覆盖不等于覆盖小红书整个平台，也不等于已经在线验证账号操作。

## 命令与接口

| CLI | 后端接口 | 行为 |
| --- | --- | --- |
| `status` | `GET /health` + `GET /api/v1/login/status` | 服务与登录状态 |
| `auth status` | `GET /api/v1/login/status` | 登录状态 |
| `auth qrcode` | `GET /api/v1/login/qrcode` | 生成扫码登录二维码 |
| `auth logout` | `DELETE /api/v1/login/cookies` | 删除后端登录 Cookies |
| `search` | `POST /api/v1/feeds/search` | 关键词与五类筛选 |
| `feed list` | `GET /api/v1/feeds/list` | 首页推荐 |
| `note get` | `POST /api/v1/feeds/detail` | 笔记与可配置评论加载 |
| `user get` | `POST /api/v1/user/profile` | 指定用户与主页分栏 |
| `user me` | `GET /api/v1/user/me` | 当前用户与主页分栏 |
| `publish image` / `draft publish` | `POST /api/v1/publish` | 图文、定时、可见范围、原创与商品 |
| `publish video` / `draft publish --video` | `POST /api/v1/publish_video` | 单视频、定时、可见范围与商品 |
| `comment add` | `POST /api/v1/feeds/comment` | 发表评论 |
| `comment reply` | `POST /api/v1/feeds/comment/reply` | 回复指定评论或用户 |
| `note like/unlike` | `POST /api/v1/feeds/like` | `unlike` 布尔值区分动作 |
| `note favorite/unfavorite` | `POST /api/v1/feeds/favorite` | `unfavorite` 布尔值区分动作 |
| `notifications unread` | `GET /api/v1/notifications/unread` | 未读计数 |
| `notifications list` | `GET /api/v1/notifications/list` | 通知分栏和条数，以查询参数传递 |
| `notifications reply` | `POST /api/v1/notifications/reply` | 按 comment_id 回复 |
| `notifications like/unlike` | `POST /api/v1/notifications/like` | 按 comment_id 点赞/取消 |
| 其他 `draft` 子命令 | 本地 SQLite | 创建、编辑、历史与文件导出 |
| `info/capabilities/config show` | 本地配置和命令元数据 | 无网络能力发现 |

搜索与通知列表上游均提供 GET/POST 两种接口。本 CLI 搜索使用 POST JSON，通知列表使用 GET 查询参数；两者均对应已核对的上游路由。`/mcp` 是后端提供给 MCP 客户端的协议入口，本 CLI 使用 REST，因此后端地址应是服务根路径，可包含反向代理前缀。

## 参数语义

- 笔记与用户引用只接受合法 ID 或完整 HTTPS 小红书链接，token 可来自 URL 或显式选项。短链接不自动请求解析。
- 搜索五类筛选映射到 `filters.sort_by/note_type/publish_time/search_scope/location`；主页 `tab` 支持 `note/fav/liked`。
- 详情高级选项放在嵌套 `comment_config`，对应 `click_more_replies/max_replies_threshold/max_comment_items/scroll_speed`，自动启用更多评论加载；`--reply-limit` 需配合 `--expand-replies`。以当前 [feed_detail.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/feed_detail.go) 运行时逻辑为准：0 回退为 20 条主评论、回复展开阈值 10；`--all-comments` 仍受目标、超时与页面可见内容限制。
- 评论回复通过 `comment_id` 或 `user_id` 定位，至少一个；通知回复与点赞使用 `comment_id`，不使用通知条目的 `id`。
- 发布的 `images` 支持后端可读路径与 HTTP(S) URL，`video` 是单个后端可读文件。CLI 不传输媒体；`--server-paths` 显式表示媒体路径属于后端文件系统。
- `schedule_at` 使用带时区 RFC3339，当前后端接受至少 1 小时后且不超过 14 天。`visibility` 保留上游中文枚举；图文才有 `is_original`。
- 发布标题沿用上游加权长度，标签规范化后最多 10 个，正文需非空。上游页面继续负责实际发布条件。商品参数交给后端搜索并选择结果，不能解释成精确 SKU 绑定保证。

## 状态与失败处理

发布、评论、互动及退出登录命令直接执行对应动作，不添加第二层确认开关。它们的 `--dry-run` 先执行同样的参数验证，再输出将提交的请求，不创建网络请求；它不是线上可行性或成功证明。`auth qrcode` 显式获取登录二维码，没有 `--dry-run`。HTTP 写入不自动重试。超时、断连或写请求执行中 Ctrl+C 可能发生在后端完成操作之后；无法确认时返回 `BACKEND_OUTCOME_UNKNOWN`、退出 1。请求前或读取过程中的普通中断仍为 130。结果不明时核对平台状态后再决定是否重试。

JSON 成功/错误 envelope 与退出码覆盖一贯的 CLI 契约，后端业务数据保持原字段及嵌套层次。错误不会回显可能含 Cookie、token、请求正文的原始异常。后端不可用时真实失败，不返回样例内容。

SQLite 事务保护本地草稿与持久化历史。CLI 创建/更新草稿时，把本地相对图片引用按当时工作目录转成绝对路径，不做存在性检查；HTTP(S) URL 和后端绝对路径原样保留。图文发布拒绝旧草稿中遗留的相对图片引用，要求从正确目录显式 `draft update --image` 修正，避免更换工作目录后误发同名图片。

`draft publish` 从草稿构建图文请求，`--video` 可改发视频并忽略草稿图片（包括遗留相对引用）；不删除草稿、不把本地文档同步成平台草稿。撤销/重做只操作本地数据库，不反向执行远程动作。导出是本地 Markdown/JSON 文档，没有冒充平台原生草稿格式。

## 明确边界

当前后端没有关注/取消关注、私信、编辑/删除已发布笔记、删除评论、运营分析、直播或店铺管理接口，也不支持自动分页。通知中的新增关注分栏和发布时商品搜索不能代替这些能力。`capabilities` 是已实现适配器能力清单，不探测某个正在运行的后端是否同版本。运行时 404 等失败保持可见。

## CLI-Anything 兼容方式

使用 PEP 420 `cli_anything.rednote` 命名空间、Click、默认 REPL、JSON 输出、安装后 subprocess 验证、bundled skill 和派生的统一 REPL 外观。独立仓库以根目录作为 `agent-harness`，用 `pyproject.toml` 打包，测试位于根目录 `tests/`。方法来源为 [HARNESS.md](https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/HARNESS.md)。

测试分为无账号单元/契约测试、真实本地文件与安装命令流程、显式运行的真实后端只读测试。没有用模拟测试冒充线上发布、互动或通知验证；各次运行记录见 [tests/TEST.md](tests/TEST.md)。

接口证据：[routes.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/routes.go)、[types.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/types.go)、[handlers_api.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/handlers_api.go)、[service.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/service.go)、[search.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/search.go)、[user_profile.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/user_profile.go)、[notification.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/notification.go)。
