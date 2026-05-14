"""PyrogramPlatformEvent：基于 kurigram 的消息事件实现。

包含消息发送、流式输出与消息反应三大核心能力，对外向 AstrBot
事件总线提供与官方 telegram 适配器一致的语义。
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any, Iterable

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import (
    At,
    File,
    Image,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.api.platform import AstrBotMessage, MessageType, PlatformMetadata
from astrbot.core.utils.metrics import Metric
from pyrogram.enums import ChatAction, ParseMode

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pyrogram import Client


def _is_gif(path: str) -> bool:
    """通过扩展名或文件头判断是否为 GIF。"""
    if path.lower().endswith(".gif"):
        return True
    try:
        with open(path, "rb") as f:
            return f.read(6) in (b"GIF87a", b"GIF89a")
    except OSError:
        return False


class PyrogramPlatformEvent(AstrMessageEvent):
    """kurigram (Pyrogram) 适配器的消息事件实现。"""

    # Telegram 单条消息的最大文本长度
    MAX_MESSAGE_LENGTH = 4096

    # 文本切分时的优先级模式
    SPLIT_PATTERNS = {
        "paragraph": re.compile(r"\n\n"),
        "line": re.compile(r"\n"),
        "sentence": re.compile(r"[.!?。！？]"),
        "word": re.compile(r"\s"),
    }

    # kurigram 的 chat action 枚举常量（必须传 ChatAction enum，不能传字符串）
    ACTION_TYPING = ChatAction.TYPING
    ACTION_UPLOAD_PHOTO = ChatAction.UPLOAD_PHOTO
    ACTION_UPLOAD_VIDEO = ChatAction.UPLOAD_VIDEO
    ACTION_UPLOAD_VOICE = ChatAction.RECORD_AUDIO
    ACTION_UPLOAD_DOCUMENT = ChatAction.UPLOAD_DOCUMENT

    ACTION_BY_TYPE: dict[type, ChatAction] = {
        Record: ACTION_UPLOAD_VOICE,
        Video: ACTION_UPLOAD_VIDEO,
        File: ACTION_UPLOAD_DOCUMENT,
        Image: ACTION_UPLOAD_PHOTO,
        Plain: ACTION_TYPING,
    }

    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: "Client",
        streaming_throttle: float = 5.0,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client
        self.streaming_throttle = streaming_throttle

    # ------------------------------------------------------------------ #
    # 文本切分与发送
    # ------------------------------------------------------------------ #
    @classmethod
    def _split_message(cls, text: str) -> list[str]:
        """按 Telegram 字数上限切分文本，尽量在段落/句子等边界拆分。"""
        if len(text) <= cls.MAX_MESSAGE_LENGTH:
            return [text]

        chunks: list[str] = []
        while text:
            if len(text) <= cls.MAX_MESSAGE_LENGTH:
                chunks.append(text)
                break

            split_point = cls.MAX_MESSAGE_LENGTH
            segment = text[: cls.MAX_MESSAGE_LENGTH]

            for _, pattern in cls.SPLIT_PATTERNS.items():
                if matches := list(pattern.finditer(segment)):
                    split_point = matches[-1].end()
                    break

            chunks.append(text[:split_point])
            text = text[split_point:].lstrip()

        return chunks

    @classmethod
    async def _send_text_chunks(
        cls,
        client: "Client",
        text: str,
        chat_id: int | str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        """把文本按上限切分后逐段以 Markdown 发送，失败回退到纯文本。

        说明：直接使用 Pyrogram 的 ``ParseMode.MARKDOWN``（传统 Markdown 模式），
        无需像 MarkdownV2 那样对 ``!``、``.``、``-`` 等普通字符做反斜杠转义，
        因此原始文本如 ``Hello! How can I help you today?`` 会被原样发送。
        """
        for chunk in cls._split_message(text):
            payload: dict[str, Any] = {"chat_id": chat_id}
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            try:
                await client.send_message(
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    **payload,
                )
            except Exception as exc:  # noqa: BLE001 - 回退到纯文本
                logger.warning(
                    f"[Pyrogram] Markdown 发送失败，回退到纯文本: {exc}"
                )
                await client.send_message(
                    text=chunk,
                    parse_mode=ParseMode.DISABLED,
                    **payload,
                )

    @classmethod
    async def _send_chat_action(
        cls,
        client: "Client",
        chat_id: int | str,
        action: ChatAction,
        message_thread_id: int | None = None,
    ) -> None:
        """发送 chat action（typing / upload_photo 等）。

        注意：``action`` 必须是 :class:`pyrogram.enums.ChatAction` 枚举成员；
        kurigram 内部会调用 ``action.name`` 与 ``action.value``，传字符串会
        触发 ``AttributeError``。
        """
        try:
            kwargs: dict[str, Any] = {"chat_id": chat_id, "action": action}
            if message_thread_id is not None:
                kwargs["message_thread_id"] = message_thread_id
            await client.send_chat_action(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Pyrogram] 发送 chat action 失败: {exc}")

    @classmethod
    def _get_chat_action_for_chain(cls, chain: Iterable[Any]) -> ChatAction:
        """根据消息链推断最合适的 chat action。"""
        chain_list = list(chain)
        for seg_type, action in cls.ACTION_BY_TYPE.items():
            if any(isinstance(seg, seg_type) for seg in chain_list):
                return action
        return cls.ACTION_TYPING

    @staticmethod
    def _split_session_id(session_id: str) -> tuple[int, int | None]:
        """将 ``chat_id#thread_id`` 形式的 session 拆为 (chat_id, thread_id)。"""
        if "#" in session_id:
            chat_part, thread_part = session_id.split("#", 1)
            try:
                return int(chat_part), int(thread_part)
            except ValueError:
                return int(chat_part), None
        try:
            return int(session_id), None
        except ValueError as exc:
            raise ValueError(
                f"[Pyrogram] 无法将 session_id '{session_id}' 解析为 chat_id"
            ) from exc

    # ------------------------------------------------------------------ #
    # 主发送入口
    # ------------------------------------------------------------------ #
    @classmethod
    async def send_with_client(
        cls,
        client: "Client",
        message: MessageChain,
        session_id: str,
    ) -> None:
        """根据 MessageChain 把内容发送到指定会话。"""
        chat_id, message_thread_id = cls._split_session_id(session_id)

        reply_to_message_id: int | None = None
        at_user_id: str | None = None
        for seg in message.chain:
            if isinstance(seg, Reply) and seg.id is not None:
                try:
                    reply_to_message_id = int(seg.id)
                except (TypeError, ValueError):
                    reply_to_message_id = None
            if isinstance(seg, At):
                if seg.name:
                    at_user_id = seg.name
                elif seg.qq is not None:
                    at_user_id = str(seg.qq)

        # 在群聊中根据消息内容显示恰当的 chat action
        action = cls._get_chat_action_for_chain(message.chain)
        await cls._send_chat_action(client, chat_id, action, message_thread_id)

        at_flag = False
        for seg in message.chain:
            if isinstance(seg, Plain):
                text = seg.text
                if at_user_id and not at_flag:
                    text = f"@{at_user_id} {text}"
                    at_flag = True
                await cls._send_text_chunks(
                    client,
                    text,
                    chat_id,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                )
            elif isinstance(seg, Image):
                path = await seg.convert_to_file_path()
                if _is_gif(path):
                    await client.send_animation(
                        chat_id=chat_id,
                        animation=path,
                        reply_to_message_id=reply_to_message_id,
                        message_thread_id=message_thread_id,
                    )
                else:
                    await client.send_photo(
                        chat_id=chat_id,
                        photo=path,
                        reply_to_message_id=reply_to_message_id,
                        message_thread_id=message_thread_id,
                    )
            elif isinstance(seg, File):
                path = await seg.get_file()
                name = seg.name or os.path.basename(path)
                await client.send_document(
                    chat_id=chat_id,
                    document=path,
                    file_name=name,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                )
            elif isinstance(seg, Record):
                path = await seg.convert_to_file_path()
                await client.send_voice(
                    chat_id=chat_id,
                    voice=path,
                    caption=getattr(seg, "text", None) or None,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                )
            elif isinstance(seg, Video):
                path = await seg.convert_to_file_path()
                await client.send_video(
                    chat_id=chat_id,
                    video=path,
                    caption=getattr(seg, "text", None) or None,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                )
            elif isinstance(seg, Reply):
                # Reply 已被前面提取为 reply_to_message_id，跳过
                continue
            elif isinstance(seg, At):
                # At 已被合并进文本中
                continue
            else:
                logger.warning(f"[Pyrogram] 暂不支持的消息组件: {type(seg).__name__}")

    async def send(self, message: MessageChain) -> None:
        """实现 AstrMessageEvent.send：选择恰当的 chat_id 把消息发出。"""
        if self.get_message_type() == MessageType.GROUP_MESSAGE:
            session_id = self.message_obj.group_id
        else:
            session_id = self.get_sender_id()
        await self.send_with_client(self.client, message, session_id)
        await super().send(message)

    # ------------------------------------------------------------------ #
    # Reaction
    # ------------------------------------------------------------------ #
    async def react(self, emoji: str | None, big: bool = False) -> None:
        """给原消息添加 Telegram emoji 反应。传入 None/"" 则清空。"""
        try:
            if self.get_message_type() == MessageType.GROUP_MESSAGE:
                chat_id = (self.message_obj.group_id or "").split("#")[0]
            else:
                chat_id = self.get_sender_id()

            chat_id_int = int(chat_id)
            message_id = int(self.message_obj.message_id)

            await self.client.send_reaction(
                chat_id=chat_id_int,
                message_id=message_id,
                emoji=emoji if emoji else None,
                big=big,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[Pyrogram] 添加反应失败: {exc}")

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        """流式输出：先 send_message 占位，再周期性 edit_message_text 更新。"""
        if self.get_message_type() == MessageType.GROUP_MESSAGE:
            session_id = self.message_obj.group_id
        else:
            session_id = self.get_sender_id()
        chat_id, message_thread_id = self._split_session_id(session_id)

        await self._send_chat_action(
            self.client, chat_id, self.ACTION_TYPING, message_thread_id
        )

        delta = ""
        message_id: int | None = None
        current_content = ""
        last_edit_time = 0.0
        last_action_time = asyncio.get_running_loop().time()
        chat_action_interval = 10.0

        def _append_text(text: str) -> None:
            nonlocal delta
            delta += text

        async for chain in generator:
            if not isinstance(chain, MessageChain):
                continue

            if chain.type == "break":
                if message_id is not None and delta:
                    try:
                        await self.client.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=delta,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"[Pyrogram] 流式 break 编辑消息失败: {exc}"
                        )
                message_id = None
                delta = ""
                current_content = ""
                continue

            await self._process_streaming_chain(
                chain,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                on_text=_append_text,
            )

            now = asyncio.get_running_loop().time()
            if now - last_action_time >= chat_action_interval:
                await self._send_chat_action(
                    self.client, chat_id, self.ACTION_TYPING, message_thread_id
                )
                last_action_time = now

            if not delta:
                continue

            if message_id is None:
                # 首次发送
                try:
                    msg = await self.client.send_message(
                        chat_id=chat_id,
                        text=delta[: self.MAX_MESSAGE_LENGTH],
                        message_thread_id=message_thread_id,
                    )
                    message_id = msg.id
                    current_content = delta[: self.MAX_MESSAGE_LENGTH]
                    last_edit_time = asyncio.get_running_loop().time()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[Pyrogram] 流式首次发送失败: {exc}")
            else:
                if (
                    asyncio.get_running_loop().time() - last_edit_time
                    < self.streaming_throttle
                ):
                    continue
                if len(delta) > self.MAX_MESSAGE_LENGTH:
                    # 超长时再启动一条新消息
                    overflow_text = delta
                    message_id = None
                    delta = ""
                    current_content = ""
                    for chunk in self._split_message(overflow_text):
                        await self._send_text_chunks(
                            self.client,
                            chunk,
                            chat_id,
                            message_thread_id=message_thread_id,
                        )
                    continue
                if delta == current_content:
                    continue
                try:
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=delta,
                    )
                    current_content = delta
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[Pyrogram] 流式编辑失败: {exc}")
                last_edit_time = asyncio.get_running_loop().time()

        # 流式结束：使用 Markdown 重新渲染最终内容（无需转义普通字符）
        if message_id is not None and delta and delta != current_content:
            try:
                await self.client.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=delta,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[Pyrogram] 流式收尾 Markdown 失败，回退纯文本: {exc}"
                )
                try:
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=delta,
                        parse_mode=ParseMode.DISABLED,
                    )
                except Exception as exc2:  # noqa: BLE001
                    logger.warning(f"[Pyrogram] 流式收尾编辑失败: {exc2}")

        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.platform_meta.name)
        )
        self._has_send_oper = True

    async def _process_streaming_chain(
        self,
        chain: MessageChain,
        *,
        chat_id: int,
        message_thread_id: int | None,
        on_text,
    ) -> None:
        """处理 streaming 过程中收到的非文本组件（直接发送，不参与编辑）。"""
        for seg in chain.chain:
            if isinstance(seg, Plain):
                on_text(seg.text)
            elif isinstance(seg, Image):
                path = await seg.convert_to_file_path()
                if _is_gif(path):
                    await self.client.send_animation(
                        chat_id=chat_id,
                        animation=path,
                        message_thread_id=message_thread_id,
                    )
                else:
                    await self.client.send_photo(
                        chat_id=chat_id,
                        photo=path,
                        message_thread_id=message_thread_id,
                    )
            elif isinstance(seg, File):
                path = await seg.get_file()
                name = seg.name or os.path.basename(path)
                await self.client.send_document(
                    chat_id=chat_id,
                    document=path,
                    file_name=name,
                    message_thread_id=message_thread_id,
                )
            elif isinstance(seg, Record):
                path = await seg.convert_to_file_path()
                await self.client.send_voice(
                    chat_id=chat_id,
                    voice=path,
                    message_thread_id=message_thread_id,
                )
            elif isinstance(seg, Video):
                path = await seg.convert_to_file_path()
                await self.client.send_video(
                    chat_id=chat_id,
                    video=path,
                    message_thread_id=message_thread_id,
                )
            else:
                logger.debug(
                    f"[Pyrogram] streaming 中忽略不支持的组件: {type(seg).__name__}"
                )


__all__ = ["PyrogramPlatformEvent"]
