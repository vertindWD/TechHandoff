from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError("机器人允许范围必须是字符串或字符串数组")


def _from_env(data: dict, field: str, required: bool = True) -> str:
    env_name = str(data.get(f"{field}_env") or "").strip()
    if not env_name:
        if required:
            raise ValueError(f"机器人缺少 {field}_env")
        return ""
    value = os.getenv(env_name, "").strip()
    if required and not value:
        raise ValueError(f"机器人环境变量 {env_name} 未配置")
    return value


@dataclass(frozen=True)
class FeishuBotBinding:
    bot_id: str
    callback_key: str
    project_id: str
    app_id: str
    app_secret: str
    verification_token: str
    transport: str = "websocket"
    tenant_domain: str = ""
    allowed_chat_ids: tuple[str, ...] = ()
    allowed_user_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "FeishuBotBinding":
        bot_id = str(data.get("bot_id") or "").strip()
        callback_key = str(data.get("callback_key") or bot_id).strip()
        project_id = str(data.get("project_id") or "").strip()
        if not bot_id:
            raise ValueError("机器人 bot_id 不能为空")
        if not _KEY_PATTERN.fullmatch(bot_id) or not _KEY_PATTERN.fullmatch(callback_key):
            raise ValueError("bot_id 和 callback_key 只能包含字母、数字、下划线和连字符")
        transport = str(data.get("transport") or "websocket").strip().lower()
        if transport not in {"websocket", "webhook"}:
            raise ValueError("机器人 transport 只能是 websocket 或 webhook")

        allowed_chat_ids = _strings(data.get("allowed_chat_ids"))
        allowed_user_ids = _strings(data.get("allowed_user_ids"))
        chat_env = str(data.get("allowed_chat_ids_env") or "").strip()
        user_env = str(data.get("allowed_user_ids_env") or "").strip()
        if chat_env:
            allowed_chat_ids = (*allowed_chat_ids, *_strings(os.getenv(chat_env, "")))
        if user_env:
            allowed_user_ids = (*allowed_user_ids, *_strings(os.getenv(user_env, "")))

        return cls(
            bot_id=bot_id,
            callback_key=callback_key,
            project_id=project_id,
            app_id=_from_env(data, "app_id"),
            app_secret=_from_env(data, "app_secret"),
            verification_token=_from_env(
                data,
                "verification_token",
                required=transport == "webhook",
            ),
            transport=transport,
            tenant_domain=str(data.get("tenant_domain") or "").rstrip("/"),
            allowed_chat_ids=tuple(dict.fromkeys(allowed_chat_ids)),
            allowed_user_ids=tuple(dict.fromkeys(allowed_user_ids)),
        )


def load_feishu_bots(path: Path | None) -> tuple[FeishuBotBinding, ...]:
    if not path or not path.is_file():
        return ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("bots", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("飞书机器人配置必须包含 bots 数组")
    bots = tuple(FeishuBotBinding.from_dict(dict(item)) for item in items)
    bot_ids = [item.bot_id for item in bots]
    callback_keys = [item.callback_key for item in bots]
    if len(set(bot_ids)) != len(bot_ids):
        raise ValueError("飞书机器人 bot_id 不能重复")
    if len(set(callback_keys)) != len(callback_keys):
        raise ValueError("飞书机器人 callback_key 不能重复")
    return bots
