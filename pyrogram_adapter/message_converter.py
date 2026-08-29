"""将 kurigram 的 Message 对象转换为 AstrBotMessage。

该模块的职责单一：把 Pyrogram/Kurigram 的 Message 解析为 AstrBot 的内部
消息模型 ``AstrBotMessage``，包含文本、图片、视频、语音、贴纸、文档、
回复链等元素。
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pyrogram import Client
    from pyrogram.types import Message


def _is_private_chat(chat_type_name: str) -> bool:
    """根据 Pyrogram ChatType 名称判断是否为私聊。"""
    return chat_type_name.upper() in {"PRIVATE", "BOT"}


class PyrogramMessageConverter:
    """负责把 kurigram 的 Message 转换为 AstrBotMessage。

    转换过程会按需把媒体下载到 AstrBot 的临时目录，并以本地路径填入
    消息组件（与官方 telegram 适配器保持一致）。
    """

    def __init__(
        self,
        client: "Client",
        *,
        max_download_size_bytes: int = 0,
    ) -> None:
        """构造消息转换器。

        Args:
            client: kurigram Client 实例。
            max_download_size_bytes: 单次下载允许的最大字节数；``0`` 表示不限制。
                超过此大小的媒体不会下载，对应消息组件将被替换为占位 ``Plain``。
        """
        self.client = client
        self.max_download_size_bytes = max(0, int(max_download_size_bytes))

    async def convert(
        self,
        message: "Message",
        *,
        bot_username: str,
        bot_id: int,
        get_reply: bool = True,
    ) -> AstrBotMessage | None:
        """把 kurigram Message 转为 AstrBotMessage。

        Args:
            message: kurigram 的原始 Message。
            bot_username: 当前 Bot 的用户名，用于处理 @ 和命令后缀。
            bot_id: 当前 Bot 的数字 ID，用于识别"回复 Bot 自身"的场景。
            get_reply: 是否递归处理 reply_to_message（避免无限嵌套）。

        Returns:
            转换后的 ``AstrBotMessage``；当消息无效或不应处理时返回 ``None``。
        """
        if message is None or message.chat is None:
            logger.warning("[Pyrogram] 收到空消息或无 chat 信息，跳过。")
            return None

        # 指向其他机器人的命令（/cmd@other_bot）不应由本 Bot 响应；
        # MTProto 下群内所有消息都会送达，这里需主动丢弃。
        if message.text and self.is_command_for_other_bot(
            message.text, bot_username=bot_username
        ):
            logger.debug(
                f"[Pyrogram] 忽略指向其他机器人的命令: {message.text.split(' ', 1)[0]}"
            )
            return None

        abm = AstrBotMessage()
        chat = message.chat
        chat_type_name = getattr(chat.type, "name", str(chat.type)).upper()

        # 会话信息：私聊 / 群聊（含话题）
        abm.session_id = str(chat.id)
        if _is_private_chat(chat_type_name):
            abm.type = MessageType.FRIEND_MESSAGE
        else:
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = str(chat.id)
            thread_id = getattr(message, "message_thread_id", None)
            if thread_id and getattr(message, "is_topic_message", False):
                abm.group_id = f"{abm.group_id}#{thread_id}"
                abm.session_id = abm.group_id

        abm.message_id = str(message.id)

        # 发送者信息
        from_user = message.from_user
        if not from_user:
            logger.warning("[Pyrogram] 收到无 from_user 的消息，跳过。")
            return None
        abm.sender = MessageMember(
            user_id=str(from_user.id),
            nickname=from_user.username or from_user.first_name or "Unknown",
        )
        abm.self_id = bot_username
        abm.raw_message = message
        abm.message_str = ""
        abm.message = []

        # 处理回复链
        reply_msg = message.reply_to_message
        if (
            get_reply
            and reply_msg
            and not (
                getattr(message, "is_topic_message", False)
                and getattr(message, "message_thread_id", None) == reply_msg.id
            )
        ):
            reply_abm = await self.convert(
                reply_msg,
                bot_username=bot_username,
                bot_id=bot_id,
                get_reply=False,
            )
            if reply_abm:
                abm.message.append(
                    Comp.Reply(
                        id=reply_abm.message_id,
                        chain=reply_abm.message,
                        sender_id=reply_abm.sender.user_id,
                        sender_nickname=reply_abm.sender.nickname,
                        time=reply_abm.timestamp,
                        message_str=reply_abm.message_str,
                        text=reply_abm.message_str,
                        qq=reply_abm.sender.user_id,
                    )
                )

        # 处理消息正文
        await self._fill_components(
            abm,
            message,
            bot_username=bot_username,
            bot_id=bot_id,
        )

        return abm

    async def _fill_components(
        self,
        abm: AstrBotMessage,
        message: "Message",
        *,
        bot_username: str,
        bot_id: int,
    ) -> None:
        """根据 kurigram Message 的字段填充 AstrBotMessage.message。"""
        if message.text:
            plain_text = self._normalize_command_text(
                message.text,
                bot_username=bot_username,
                is_group=abm.type == MessageType.GROUP_MESSAGE,
                reply_to_bot=self._is_reply_to_bot(message, bot_id),
            )
            self._append_mentions(abm, message, plain_text, bot_username)
            if plain_text:
                abm.message.append(Comp.Plain(plain_text))
            abm.message_str = plain_text
            return

        # 媒体类型：依次判断
        caption_text = message.caption or ""

        if message.photo:
            local_path = await self._download_media(message)
            if local_path:
                abm.message.append(Comp.Image(file=local_path, url=local_path))
            self._apply_caption(abm, message, caption_text, bot_username)
            return

        if message.sticker:
            local_path = await self._download_media(message)
            if local_path:
                abm.message.append(Comp.Image(file=local_path, url=local_path))
            emoji = getattr(message.sticker, "emoji", None)
            if emoji:
                sticker_text = f"Sticker: {emoji}"
                abm.message_str = sticker_text
                abm.message.append(Comp.Plain(sticker_text))
            return

        if message.animation:
            local_path = await self._download_media(message)
            if local_path:
                abm.message.append(Comp.Image(file=local_path, url=local_path))
            self._apply_caption(abm, message, caption_text, bot_username)
            return

        if message.voice:
            local_path = await self._download_media(message)
            if local_path:
                record = Comp.Record(file=local_path, url=local_path)
                record.path = local_path
                abm.message.append(record)
            return

        if message.audio:
            local_path = await self._download_media(message)
            if local_path:
                file_name = (
                    getattr(message.audio, "file_name", None)
                    or f"{uuid.uuid4().hex}.mp3"
                )
                abm.message.append(
                    Comp.File(file=local_path, name=file_name, url=local_path)
                )
            self._apply_caption(abm, message, caption_text, bot_username)
            return

        if message.video or message.video_note:
            local_path = await self._download_media(message)
            if local_path:
                abm.message.append(Comp.Video(file=local_path, path=local_path))
            self._apply_caption(abm, message, caption_text, bot_username)
            return

        if message.document:
            local_path = await self._download_media(message)
            file_name = (
                getattr(message.document, "file_name", None)
                or os.path.basename(local_path or "")
                or uuid.uuid4().hex
            )
            if local_path:
                abm.message.append(
                    Comp.File(file=local_path, name=file_name, url=local_path)
                )
            self._apply_caption(abm, message, caption_text, bot_username)
            return

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_command_for_other_bot(text: str, *, bot_username: str) -> bool:
        """判断以 ``/`` 开头的文本是否为指向其他机器人的命令。

        ``/cmd`` 与 ``/cmd@本Bot用户名`` 会正常处理；
        ``/cmd@其他用户名`` 返回 ``True``（不应由本 Bot 响应）。
        """
        if not text or not text.startswith("/"):
            return False
        if not bot_username:
            # 尚未获知自身用户名时无法判断，保守放行
            return False
        head = text.split(" ", 1)[0]
        if "@" not in head:
            return False
        target = head.split("@", 1)[1].strip().lstrip("@").lower()
        if not target:
            return False
        return target != bot_username.lower()

    @staticmethod
    def _is_reply_to_bot(message: "Message", bot_id: int) -> bool:
        reply = message.reply_to_message
        if not reply or not reply.from_user:
            return False
        return reply.from_user.id == bot_id

    @staticmethod
    def _normalize_command_text(
        text: str,
        *,
        bot_username: str,
        is_group: bool,
        reply_to_bot: bool,
    ) -> str:
        """处理命令尾缀（/cmd@BotName）以及群聊中的隐式 @Bot。"""
        plain_text = text

        if is_group and reply_to_bot:
            plain_text = f"/@{bot_username} {plain_text}"

        if plain_text.startswith("/"):
            parts = plain_text.split(" ", 1)
            head = parts[0]
            if "@" in head:
                cmd, suffix = head.split("@", 1)
                if suffix.lower() == bot_username.lower():
                    tail = f" {parts[1]}" if len(parts) > 1 else ""
                    plain_text = f"{cmd}{tail}"

        return plain_text

    @staticmethod
    def _normalize_bot_mention(mention_text: str, bot_username: str) -> str:
        """Telegram 用户名不区分大小写，命中 Bot 自身时统一为规范用户名。

        AstrBot 的唤醒检查会以 ``At.qq == self_id`` 的大小写敏感方式比较，
        若按用户输入的原样保留，``@bot_useRname`` 这类大小写不一致的提及
        将无法唤醒 Bot。
        """
        if bot_username and mention_text.lower() == bot_username.lower():
            return bot_username
        return mention_text

    @staticmethod
    def _append_mentions(
        abm: AstrBotMessage,
        message: "Message",
        plain_text: str,
        bot_username: str,
    ) -> None:
        """根据 entities 收集 @ 提及，并把针对本 Bot 的提及统一为规范用户名。"""
        entities = getattr(message, "entities", None) or []
        for entity in entities:
            entity_type = getattr(entity, "type", None)
            type_name = getattr(entity_type, "name", str(entity_type)).upper()
            if type_name != "MENTION":
                continue
            offset = getattr(entity, "offset", 0) or 0
            length = getattr(entity, "length", 0) or 0
            mention_text = plain_text[offset + 1 : offset + length]
            if not mention_text:
                continue
            mention_text = PyrogramMessageConverter._normalize_bot_mention(
                mention_text, bot_username
            )
            abm.message.append(Comp.At(qq=mention_text, name=mention_text))

    @staticmethod
    def _apply_caption(
        abm: AstrBotMessage,
        message: "Message",
        caption_text: str,
        bot_username: str,
    ) -> None:
        """把媒体消息的 caption 作为附加文本写入消息链。"""
        if not caption_text:
            return
        # 指向其他机器人的命令式 caption 不应写入并触发唤醒
        if PyrogramMessageConverter.is_command_for_other_bot(caption_text, bot_username=bot_username):
            return
        abm.message_str = caption_text
        abm.message.append(Comp.Plain(caption_text))

        entities = getattr(message, "caption_entities", None) or []
        for entity in entities:
            entity_type = getattr(entity, "type", None)
            type_name = getattr(entity_type, "name", str(entity_type)).upper()
            if type_name != "MENTION":
                continue
            offset = getattr(entity, "offset", 0) or 0
            length = getattr(entity, "length", 0) or 0
            mention_text = caption_text[offset + 1 : offset + length]
            if mention_text:
                mention_text = PyrogramMessageConverter._normalize_bot_mention(
                    mention_text, bot_username
                )
                abm.message.append(Comp.At(qq=mention_text, name=mention_text))

    async def _download_media(self, message: "Message") -> str | None:
        """下载消息中的媒体到 AstrBot 临时目录，返回本地路径。

        当设置了下载大小上限且媒体预估大小超过限制时，会跳过下载并返回 ``None``，
        同时记录一条警告日志。失败时同样返回 ``None``。
        这层封装便于在测试中通过 mock 替换。
        """
        media_size = self._extract_media_size(message)
        if (
            self.max_download_size_bytes > 0
            and media_size is not None
            and media_size > self.max_download_size_bytes
        ):
            size_mb = media_size / (1024 * 1024)
            limit_mb = self.max_download_size_bytes / (1024 * 1024)
            logger.warning(
                f"[Pyrogram] 媒体大小 {size_mb:.2f} MB 超过下载上限 {limit_mb:.0f} MB，已跳过下载。"
            )
            return None
        try:
            temp_dir = get_astrbot_temp_path()
            os.makedirs(temp_dir, exist_ok=True)
            target = os.path.join(temp_dir, f"pyrogram_{uuid.uuid4().hex}")
            # kurigram 在 file_name 缺省时会自行决定扩展名，返回最终路径
            result = await self.client.download_media(message, file_name=target)
            return str(result) if result else None
        except Exception:
            logger.exception("[Pyrogram] 下载媒体失败")
            return None

    @staticmethod
    def _extract_media_size(message: "Message") -> int | None:
        """从 kurigram Message 中提取媒体文件的预估字节数。

        依次尝试 ``document / audio / video / video_note / animation /
        voice / sticker / photo`` 的 ``file_size`` 属性；找不到时返回 ``None``。
        """
        for attr in (
            "document",
            "audio",
            "video",
            "video_note",
            "animation",
            "voice",
            "sticker",
            "photo",
        ):
            media = getattr(message, attr, None)
            if media is None:
                continue
            size = getattr(media, "file_size", None)
            if isinstance(size, int) and size > 0:
                return size
        return None


__all__ = ["PyrogramMessageConverter"]
