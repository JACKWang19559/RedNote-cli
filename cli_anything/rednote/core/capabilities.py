"""The supported backend contract, distinct from the whole Xiaohongshu platform."""

OPERATIONS = [
    ("status", "GET", "/health", False),
    ("auth status", "GET", "/api/v1/login/status", False),
    ("auth qrcode", "GET", "/api/v1/login/qrcode", True),
    ("auth logout", "DELETE", "/api/v1/login/cookies", True),
    ("publish image / draft publish", "POST", "/api/v1/publish", True),
    ("publish video / draft publish --video", "POST", "/api/v1/publish_video", True),
    ("feed list", "GET", "/api/v1/feeds/list", False),
    ("search", "POST", "/api/v1/feeds/search", False),
    ("note get", "POST", "/api/v1/feeds/detail", False),
    ("user get", "POST", "/api/v1/user/profile", False),
    ("comment add", "POST", "/api/v1/feeds/comment", True),
    ("comment reply", "POST", "/api/v1/feeds/comment/reply", True),
    ("note like / unlike", "POST", "/api/v1/feeds/like", True),
    ("note favorite / unfavorite", "POST", "/api/v1/feeds/favorite", True),
    ("user me", "GET", "/api/v1/user/me", False),
    ("notifications unread", "GET", "/api/v1/notifications/unread", False),
    ("notifications list", "GET", "/api/v1/notifications/list", False),
    ("notifications reply", "POST", "/api/v1/notifications/reply", True),
    ("notifications like / unlike", "POST", "/api/v1/notifications/like", True),
]


def describe_capabilities() -> dict:
    return {
        "backend": "xpzouying/xiaohongshu-mcp REST",
        "contract_checked": "2026-10-28",
        "source": "https://github.com/xpzouying/xiaohongshu-mcp/blob/main/routes.go",
        "runtime_probed": False,
        "operations": [
            {
                "command": command,
                "method": method,
                "path": path,
                "mutates_remote": mutation,
            }
            for command, method, path, mutation in OPERATIONS
        ],
        "local": [
            "draft CRUD",
            "draft undo/redo",
            "draft export",
            "REPL",
            "JSON",
            "write dry-run",
        ],
        "unsupported": [
            {"feature": feature, "reason": "当前后端未提供对应接口"}
            for feature in [
                "私信",
                "关注/取消关注",
                "编辑/删除已发布笔记",
                "删除评论",
                "创作中心数据分析",
                "直播管理",
                "店铺订单管理",
                "自动分页",
            ]
        ],
        "notes": [
            "功能表是适配器契约，不代表当前后端已启动或版本已匹配。",
            "GET/POST 是传输方式，读写属性以 mutates_remote 为准。",
            "发布、评论等写操作支持 --dry-run；登录二维码由 auth qrcode 显式获取。",
        ],
    }
