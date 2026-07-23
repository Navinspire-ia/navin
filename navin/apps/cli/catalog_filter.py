"""Filter CLI Apps catalog entries that should not appear in the product UI."""

from __future__ import annotations

import re
from typing import Any

# China-market / CN-vendor CLI apps removed from the Apps catalog surface.
# Names are matched case-insensitively after normalizing `_` → `-`.
RETIRED_CLI_APPS = frozenset(
    {
        "dify",
        "dify-workflow",
        "feishu",
        "jimeng",
        "minimax",
        "minimax-cli",
        "mubu",
        "wecom",
        "wechat",
        "weixin",
        "dingtalk",
        "dingding",
        "lark",
        "larksuite",
        "coze",
        "doubao",
        "qwen",
        "tongyi",
        "dashscope",
        "zhipu",
        "moonshot",
        "kimi",
        "baidu",
        "tencent",
        "aliyun",
        "alibaba",
        "bytedance",
        "byteplus",
        "volcengine",
        "bilibili",
        "xiaohongshu",
        "douyin",
        "kuaishou",
        "youdao",
        "wps",
        "qq",
    }
)

_HANZI_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_cli_app_name(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def is_retired_cli_app(name: str) -> bool:
    return normalize_cli_app_name(name) in RETIRED_CLI_APPS


def is_china_market_cli_app(app: dict[str, Any]) -> bool:
    """Return True when the catalog row should be hidden from Apps."""
    name = normalize_cli_app_name(str(app.get("name") or ""))
    if not name:
        return False
    if is_retired_cli_app(name):
        return True
    # Hide entries whose primary labels are Chinese-script (CN-local products).
    display = str(app.get("display_name") or "")
    if _HANZI_RE.search(name) or _HANZI_RE.search(display):
        return True
    return False


def filter_cli_catalog(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [app for app in apps if not is_china_market_cli_app(app)]
