"""适配器的配置管理。

定义默认配置模板、WebUI 元数据，以及配置解析和校验逻辑。
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 默认配置模板（注册到 AstrBot WebUI 用于生成表单）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_TEMPLATE: dict[str, Any] = {
    "id": "pyrogram_bot",
    "api_id": 0,
    "api_hash": "",
    "bot_token": "",
    "in_memory": True,
    "workdir": "",
    "start_message": "Hello! I'm a bot powered by AstrBot & kurigram.",
    "pyrogram_command_register": True,
    "pyrogram_command_auto_refresh": True,
    "pyrogram_command_register_interval": 300,
    "pyrogram_media_group_timeout": 2.5,
    "pyrogram_media_group_max_wait": 10.0,
    "pyrogram_streaming_throttle": 5.0,
    "test_mode": False,
}


# ---------------------------------------------------------------------------
# WebUI 配置元数据
# ---------------------------------------------------------------------------
PYROGRAM_CONFIG_METADATA: dict[str, dict[str, Any]] = {
    "id": {
        "type": "string",
        "description": "适配器实例 ID，用于在 AstrBot 中唯一标识。",
        "hint": "建议保持默认或使用易识别名称。",
    },
    "api_id": {
        "type": "int",
        "description": "Telegram API ID（通过 https://my.telegram.org 获取）。",
    },
    "api_hash": {
        "type": "string",
        "description": "Telegram API Hash（通过 https://my.telegram.org 获取）。",
    },
    "bot_token": {
        "type": "string",
        "description": "BotFather 申请的机器人 Token，例如 123456:ABCDEF...",
    },
    "in_memory": {
        "type": "bool",
        "description": "是否使用 in-memory 会话（True 则不持久化 session 文件）。",
    },
    "workdir": {
        "type": "string",
        "description": "session 文件存放目录，留空则使用当前工作目录。仅当 in_memory=False 时使用。",
    },
    "start_message": {
        "type": "text",
        "description": "用户向 Bot 发送 /start 时机器人的回复内容。",
    },
    "pyrogram_command_register": {
        "type": "bool",
        "description": "是否自动向 Telegram 注册 AstrBot 中已声明的指令。",
    },
    "pyrogram_command_auto_refresh": {
        "type": "bool",
        "description": "是否周期性地刷新指令注册，便于插件热加载后立即生效。",
    },
    "pyrogram_command_register_interval": {
        "type": "int",
        "description": "指令自动刷新的时间间隔（秒）。",
    },
    "pyrogram_media_group_timeout": {
        "type": "float",
        "description": "媒体组（相册）的去抖延迟（秒），等待消息全部到达后再合并处理。",
    },
    "pyrogram_media_group_max_wait": {
        "type": "float",
        "description": "媒体组的最长等待时间（秒），用于防止无限等待。",
    },
    "pyrogram_streaming_throttle": {
        "type": "float",
        "description": "群聊流式输出的最小编辑间隔（秒），避免触发 Telegram 速率限制。",
    },
    "test_mode": {
        "type": "bool",
        "description": "是否连接 Telegram 测试服务器（仅供调试用）。开启后，session 名称会自动追加 _test 后缀以区分正式环境。",
    },
}


def parse_bool(value: Any, default: bool) -> bool:
    """将任意值转换为 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "":
            return default
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def parse_str(value: Any, default: str = "") -> str:
    """将任意值转换为非空字符串，否则返回默认值。"""
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized if normalized else default


def parse_int(value: Any, default: int = 0) -> int:
    """将任意值转换为 int，失败时返回默认值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        try:
            return int(normalized)
        except ValueError:
            return default
    return default


def parse_float(value: Any, default: float = 0.0) -> float:
    """将任意值转换为 float，失败时返回默认值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        try:
            return float(normalized)
        except ValueError:
            return default
    return default


class PyrogramAdapterConfig:
    """适配器解析后的强类型配置。

    通过 ``from_dict`` 工厂方法从原始 ``platform_config`` 转换而来；
    构造时即完成校验，校验失败时抛出 :class:`ValueError`。
    """

    def __init__(
        self,
        *,
        adapter_id: str,
        api_id: int,
        api_hash: str,
        bot_token: str,
        in_memory: bool,
        workdir: str,
        start_message: str,
        command_register: bool,
        command_auto_refresh: bool,
        command_register_interval: int,
        media_group_timeout: float,
        media_group_max_wait: float,
        streaming_throttle: float,
        test_mode: bool,
    ) -> None:
        self.adapter_id = adapter_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.in_memory = in_memory
        self.workdir = workdir
        self.start_message = start_message
        self.command_register = command_register
        self.command_auto_refresh = command_auto_refresh
        self.command_register_interval = command_register_interval
        self.media_group_timeout = media_group_timeout
        self.media_group_max_wait = media_group_max_wait
        self.streaming_throttle = streaming_throttle
        self.test_mode = test_mode

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PyrogramAdapterConfig":
        """从原始配置 dict 创建配置对象，同时执行校验。"""
        raw = raw or {}
        adapter_id = parse_str(raw.get("id"), "pyrogram_bot")
        api_id = parse_int(raw.get("api_id"), 0)
        api_hash = parse_str(raw.get("api_hash"), "")
        bot_token = parse_str(raw.get("bot_token"), "")
        in_memory = parse_bool(raw.get("in_memory"), True)
        workdir = parse_str(raw.get("workdir"), "")
        start_message = parse_str(
            raw.get("start_message"),
            "Hello! I'm a bot powered by AstrBot & kurigram.",
        )
        command_register = parse_bool(raw.get("pyrogram_command_register"), True)
        command_auto_refresh = parse_bool(
            raw.get("pyrogram_command_auto_refresh"), True
        )
        command_register_interval = max(
            10, parse_int(raw.get("pyrogram_command_register_interval"), 300)
        )
        media_group_timeout = max(
            0.0, parse_float(raw.get("pyrogram_media_group_timeout"), 2.5)
        )
        media_group_max_wait = max(
            0.1, parse_float(raw.get("pyrogram_media_group_max_wait"), 10.0)
        )
        streaming_throttle = max(
            0.0, parse_float(raw.get("pyrogram_streaming_throttle"), 5.0)
        )
        test_mode = parse_bool(raw.get("test_mode"), False)

        cfg = cls(
            adapter_id=adapter_id,
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            in_memory=in_memory,
            workdir=workdir,
            start_message=start_message,
            command_register=command_register,
            command_auto_refresh=command_auto_refresh,
            command_register_interval=command_register_interval,
            media_group_timeout=media_group_timeout,
            media_group_max_wait=media_group_max_wait,
            streaming_throttle=streaming_throttle,
            test_mode=test_mode,
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """对解析后的配置执行严格校验，缺失关键字段时抛错。"""
        if self.api_id <= 0:
            raise ValueError(
                "[pyrogram_adapter] 配置项 api_id 必须为正整数，请前往 "
                "https://my.telegram.org 获取。"
            )
        if not self.api_hash:
            raise ValueError(
                "[pyrogram_adapter] 配置项 api_hash 不能为空，请前往 "
                "https://my.telegram.org 获取。"
            )
        if not self.bot_token or ":" not in self.bot_token:
            raise ValueError(
                "[pyrogram_adapter] 配置项 bot_token 不合法，请填入 BotFather "
                "颁发的 Token（形如 123456:ABCDEF...）。"
            )
        if self.media_group_max_wait <= 0:
            raise ValueError(
                "[pyrogram_adapter] pyrogram_media_group_max_wait 必须大于 0。"
            )
