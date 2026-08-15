"""PyrogramPlatformAdapter：基于 kurigram 的 Telegram Bot 适配器。

仅实现 bot 相关功能，使用 ``bot_token + api_id + api_hash`` 完成认证；
不涉及 userbot 场景。负责：

* 启动并维护 kurigram ``Client`` 的生命周期；
* 注册消息处理器，将 Telegram 消息转换为 ``AstrBotMessage`` 并入队；
* 聚合媒体组（media group / 相册）；
* 周期性向 Telegram 注册 AstrBot 中声明的指令；
* 处理 ``/start`` 命令。
"""

from __future__ import annotations

import asyncio
import re
import sys
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.platform import (
    AstrBotMessage,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .config import (
    DEFAULT_CONFIG_TEMPLATE,
    PYROGRAM_CONFIG_METADATA,
    PyrogramAdapterConfig,
)
from .plugin_info import PLUGIN_NAME
from .message_converter import PyrogramMessageConverter
from .pyrogram_event import PyrogramPlatformEvent, PyrogramPlatformGuestEvent

if sys.version_info >= (3, 12):
    from typing import override
else:  # pragma: no cover - Python < 3.12 兼容
    from typing_extensions import override

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pyrogram import Client
    from pyrogram.types import BotCommand as PyrogramBotCommand
    from pyrogram.types import Message


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


@register_platform_adapter(
    "pyrogram_bot",
    "Kurigram-based Telegram Bot 适配器",
    default_config_tmpl=DEFAULT_CONFIG_TEMPLATE,
    config_metadata=PYROGRAM_CONFIG_METADATA,
    adapter_display_name="Telegram Bot (Pyrogram/Kurigram)",
    logo_path="logo.png",
    support_streaming_message=True,
)
class PyrogramPlatformAdapter(Platform):
    """kurigram Bot 适配器主体。"""

    PLATFORM_NAME = "pyrogram_bot"

    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        self.adapter_config = PyrogramAdapterConfig.from_dict(platform_config)

        # 运行期状态
        self.client: "Client | None" = None
        self._terminating = False
        self._bot_username = ""
        self._bot_id: int = 0
        self._last_command_hash: int | None = None
        self.media_group_cache: dict[str, dict[str, Any]] = {}

        # 定时任务：用于媒体组延迟处理与指令刷新
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_listener(
            lambda ev: logger.error(
                "[Pyrogram] Scheduled job %s raised: %s",
                ev.job_id,
                ev.exception,
                exc_info=ev.exception,
            ),
            EVENT_JOB_ERROR,
        )

        self._converter: PyrogramMessageConverter | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @override
    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name=self.PLATFORM_NAME,
            description="Kurigram-based Telegram Bot 适配器",
            id=self.adapter_config.adapter_id,
            support_streaming_message=True,
        )

    @override
    async def run(self) -> None:
        """启动适配器并阻塞直到 :meth:`terminate` 被调用。"""
        # 延迟导入以避免在未安装依赖时影响其它适配器加载
        from pyrogram import Client, filters
        from pyrogram.handlers import MessageHandler

        self.client = self._build_client(Client)
        self._converter = PyrogramMessageConverter(
            self.client,
            max_download_size_bytes=self.adapter_config.max_download_size_bytes,
        )

        # 注册消息处理器：捕获所有消息再分发
        self.client.add_handler(MessageHandler(self._on_message, filters.incoming))

        # 注册 Guest Message 处理器（未进群被 @ / 回复时收到的消息）
        try:
            from pyrogram.handlers import GuestMessageHandler

            self.client.add_handler(GuestMessageHandler(self._on_guest_message))
        except ImportError:
            logger.warning(
                "[Pyrogram] 当前 kurigram 版本不支持 GuestMessageHandler，已跳过注册。"
            )

        try:
            await self.client.start()
        except Exception:
            logger.exception("[Pyrogram] 启动客户端失败")
            raise

        me = await self.client.get_me()
        self._bot_username = me.username or ""
        self._bot_id = int(me.id)
        logger.info(f"[Pyrogram] Bot 已上线: @{self._bot_username} (id={self._bot_id})")

        if self.adapter_config.command_register:
            await self.register_commands()

        self._start_command_scheduler()

        # 进入轮询等待阶段，直至 terminate
        while not self._terminating:
            await asyncio.sleep(1)

    @override
    async def terminate(self) -> None:
        try:
            self._terminating = True
            if self.scheduler.running:
                with suppress(Exception):
                    self.scheduler.shutdown(wait=False)
            if self.client is not None:
                with suppress(Exception):
                    if self.adapter_config.command_register:
                        await self.client.delete_bot_commands()
                with suppress(Exception):
                    await self.client.stop()
            logger.info("[Pyrogram] 适配器已关闭。")
        except Exception:
            logger.exception("[Pyrogram] 关闭适配器时发生错误")

    def get_client(self) -> "Client | None":
        return self.client

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _build_client(self, client_cls: type) -> "Client":
        """构造 kurigram Client 对象。"""
        cfg = self.adapter_config
        session_name = cfg.adapter_id or "astrbot_pyrogram_bot"
        if cfg.test_mode:
            session_name = f"{session_name}_test"
        kwargs: dict[str, Any] = {
            "name": session_name,
            "api_id": cfg.api_id,
            "api_hash": cfg.api_hash,
            "bot_token": cfg.bot_token,
            "in_memory": cfg.in_memory,
        }
        if cfg.test_mode:
            kwargs["test_mode"] = True
        workdir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        if cfg.workdir and not cfg.in_memory:
            workdir = cfg.workdir
        kwargs["workdir"] = workdir
        return client_cls(**kwargs)

    def _start_command_scheduler(self) -> None:
        if not self.adapter_config.command_auto_refresh:
            return
        if not self.adapter_config.command_register:
            return
        if self.scheduler.running:
            return
        self.scheduler.add_job(
            self.register_commands,
            "interval",
            seconds=self.adapter_config.command_register_interval,
            id="pyrogram_command_register",
            misfire_grace_time=60,
        )
        self.scheduler.start()

    # ------------------------------------------------------------------ #
    # Event entrypoints
    # ------------------------------------------------------------------ #
    async def _on_message(self, client: "Client", message: "Message") -> None:
        """kurigram MessageHandler 的回调。"""
        try:
            logger.debug(f"[Pyrogram] 收到消息: chat={message.chat.id} id={message.id}")

            # 媒体组：进入聚合逻辑
            if message.media_group_id:
                await self._handle_media_group_message(message)
                return

            await self._process_single_message(message)
        except Exception:
            logger.exception("[Pyrogram] 处理消息时发生异常")

    async def _process_single_message(self, message: "Message") -> None:
        """处理普通（非媒体组）消息。"""
        # /start 直接走自定义欢迎语
        if message.text and message.text.strip() == "/start":
            await self._handle_start(message)
            return

        abm = await self._converter.convert(
            message,
            bot_username=self._bot_username,
            bot_id=self._bot_id,
        )
        if abm is None:
            return
        await self._dispatch(abm)

    async def _handle_start(self, message: "Message") -> None:
        try:
            await message.reply(self.adapter_config.start_message, quote=True)
        except Exception:
            logger.exception("[Pyrogram] 回复 /start 失败")

    async def _dispatch(self, abm: AstrBotMessage) -> None:
        event = PyrogramPlatformEvent(
            message_str=abm.message_str,
            message_obj=abm,
            platform_meta=self.meta(),
            session_id=abm.session_id,
            client=self.client,
            streaming_throttle=self.adapter_config.streaming_throttle,
        )
        self.commit_event(event)

    # ------------------------------------------------------------------ #
    # Guest message handling
    # ------------------------------------------------------------------ #
    async def _on_guest_message(self, client: "Client", message: "Message") -> None:
        """kurigram GuestMessageHandler 的回调。

        Guest message 仅在 Bot 未加入的群组/私聊中、被 @ 或被回复时送达，
        因此不可能收到 media_group 类型，这里只处理单条消息。
        """
        try:
            logger.debug(
                f"[Pyrogram] 收到 guest 消息: chat={message.chat.id} "
                f"id={message.id} query_id={getattr(message, 'guest_query_id', None)}"
            )
            await self._process_single_guest_message(message)
        except Exception:
            logger.exception("[Pyrogram] 处理 guest 消息时发生异常")

    async def _process_single_guest_message(self, message: "Message") -> None:
        """处理 guest message 并派发到 AstrBot。"""
        guest_query_id = getattr(message, "guest_query_id", None)
        if not guest_query_id:
            logger.warning("[Pyrogram] guest 消息缺少 guest_query_id，跳过。")
            return

        # 由于 guest message 只投递 @机器人 / 回复机器人 的消息，
        # 指令类消息中通常会带有 @botusername 后缀，会让 AstrBot 指令解析失败，
        # 此处在无回复语境下移除 @bot 前缀/后缀。
        if (
            message.text
            and message.text.startswith("/")
            and not message.reply_to_message
            and self._bot_username
        ):
            cleaned = re.sub(
                rf"\s*@{re.escape(self._bot_username)}\b",
                "",
                message.text,
                flags=re.IGNORECASE,
            ).strip()
            if cleaned != message.text:
                logger.debug(
                    f"[Pyrogram] guest 指令清洗: '{message.text}' -> '{cleaned}'"
                )
                message.text = cleaned

        abm = await self._converter.convert(
            message,
            bot_username=self._bot_username,
            bot_id=self._bot_id,
        )
        if abm is None:
            return
        await self._dispatch_guest(abm, guest_query_id=str(guest_query_id))

    async def _dispatch_guest(
        self, abm: AstrBotMessage, *, guest_query_id: str
    ) -> None:
        event = PyrogramPlatformGuestEvent(
            message_str=abm.message_str,
            message_obj=abm,
            platform_meta=self.meta(),
            session_id=abm.session_id,
            client=self.client,
            guest_query_id=guest_query_id,
            streaming_throttle=self.adapter_config.streaming_throttle,
        )
        self.commit_event(event)

    # ------------------------------------------------------------------ #
    # Media group aggregation
    # ------------------------------------------------------------------ #
    async def _handle_media_group_message(self, message: "Message") -> None:
        """缓存媒体组成员并安排去抖处理。"""
        media_group_id = str(message.media_group_id)
        entry = self.media_group_cache.setdefault(
            media_group_id,
            {"created_at": datetime.now(), "items": []},
        )
        entry["items"].append(message)

        elapsed = (datetime.now() - entry["created_at"]).total_seconds()
        if elapsed >= self.adapter_config.media_group_max_wait:
            delay = 0.0
        else:
            delay = self.adapter_config.media_group_timeout

        if not self.scheduler.running:
            self.scheduler.start()

        job_id = f"pyrogram_media_group_{media_group_id}"
        self.scheduler.add_job(
            self._process_media_group,
            "date",
            run_date=datetime.now() + timedelta(seconds=delay),
            args=[media_group_id],
            id=job_id,
            replace_existing=True,
        )

    async def _process_media_group(self, media_group_id: str) -> None:
        """聚合媒体组所有消息后作为单条 AstrBotMessage 派发。"""
        entry = self.media_group_cache.pop(media_group_id, None)
        if not entry or not entry["items"]:
            return
        items: list["Message"] = entry["items"]
        logger.info(f"[Pyrogram] 聚合媒体组 {media_group_id}，共 {len(items)} 条消息")

        try:
            first = items[0]
            abm = await self._converter.convert(
                first,
                bot_username=self._bot_username,
                bot_id=self._bot_id,
            )
            if abm is None:
                return
            for extra in items[1:]:
                extra_abm = await self._converter.convert(
                    extra,
                    bot_username=self._bot_username,
                    bot_id=self._bot_id,
                    get_reply=False,
                )
                if extra_abm:
                    abm.message.extend(extra_abm.message)
            await self._dispatch(abm)
        except Exception:
            logger.exception(f"[Pyrogram] 处理媒体组 {media_group_id} 失败")

    # ------------------------------------------------------------------ #
    # Command registration
    # ------------------------------------------------------------------ #
    async def register_commands(self) -> None:
        """收集 AstrBot 中注册的指令并注册到 Telegram。"""
        if self.client is None:
            return
        try:
            commands = self.collect_commands()
            if not commands:
                return
            current_hash = hash(
                tuple((c["command"], c["description"]) for c in commands)
            )
            if current_hash == self._last_command_hash:
                return
            self._last_command_hash = current_hash

            from pyrogram.types import BotCommand as PyrogramBotCommand

            cmd_list: list["PyrogramBotCommand"] = [
                PyrogramBotCommand(c["command"], c["description"]) for c in commands
            ]
            with suppress(Exception):
                await self.client.delete_bot_commands()
            await self.client.set_bot_commands(cmd_list)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[Pyrogram] 向 Telegram 注册指令失败: {exc}")

    def collect_commands(self) -> list[dict[str, str]]:
        """从 AstrBot 的全局注册表里搜集合法的 Telegram 指令。"""
        command_dict: dict[str, str] = {}
        skip = {"start"}

        for handler_metadata in star_handlers_registry:
            module_path = handler_metadata.handler_module_path
            if module_path not in star_map or not star_map[module_path].activated:
                continue
            if not handler_metadata.enabled:
                continue
            for event_filter in handler_metadata.event_filters:
                info = self._extract_command_info(event_filter, handler_metadata, skip)
                if not info:
                    continue
                for name, desc in info:
                    command_dict.setdefault(name, desc)

        return [
            {"command": name, "description": command_dict[name]}
            for name in sorted(command_dict)
        ]

    @staticmethod
    def _extract_command_info(
        event_filter: Any,
        handler_metadata: Any,
        skip_commands: set[str],
    ) -> list[tuple[str, str]] | None:
        cmd_names: list[str] = []
        is_group = False
        if isinstance(event_filter, CommandFilter) and event_filter.command_name:
            if (
                event_filter.parent_command_names
                and event_filter.parent_command_names != [""]
            ):
                return None
            cmd_names = [event_filter.command_name]
            if event_filter.alias:
                cmd_names.extend(event_filter.alias)
        elif isinstance(event_filter, CommandGroupFilter):
            if event_filter.parent_group:
                return None
            cmd_names = [event_filter.group_name]
            is_group = True

        result: list[tuple[str, str]] = []
        for cmd_name in cmd_names:
            if not cmd_name or cmd_name in skip_commands:
                continue
            if not re.match(r"^[a-z0-9_]+$", cmd_name) or len(cmd_name) > 32:
                continue
            description = handler_metadata.desc or (
                f"Command group: {cmd_name}" if is_group else f"Command: {cmd_name}"
            )
            if len(description) > 30:
                description = description[:30] + "..."
            result.append((cmd_name, description))
        return result or None

    # ------------------------------------------------------------------ #
    # Outbound API (send_by_session)
    # ------------------------------------------------------------------ #
    @override
    async def send_by_session(
        self,
        session: MessageSesion,
        message_chain: MessageChain,
    ) -> None:
        if self.client is None:
            logger.warning("[Pyrogram] 客户端尚未启动，无法通过 session 发送消息")
            return
        await PyrogramPlatformEvent.send_with_client(
            self.client,
            message_chain,
            session.session_id,
        )
        await super().send_by_session(session, message_chain)


__all__ = ["PyrogramPlatformAdapter"]
