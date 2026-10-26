"""tests/test_message_converter.py - PyrogramMessageConverter 测试。"""
from __future__ import annotations

import os
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import astrbot.api.message_components as Comp
from astrbot.api.platform import MessageType
from pyrogram_adapter.message_converter import (
    PyrogramMessageConverter,
    collect_bot_usernames,
)


class _ChatType(str, Enum):
    PRIVATE = "PRIVATE"
    GROUP = "GROUP"
    SUPERGROUP = "SUPERGROUP"


def _make_user(user_id: int, username: str = "alice") -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        username=username,
        first_name=username,
        full_name=username,
    )


def _make_chat(chat_id: int, chat_type: _ChatType = _ChatType.PRIVATE) -> SimpleNamespace:
    return SimpleNamespace(id=chat_id, type=chat_type, title="chat")


_UNSET = object()


def _make_message(
    *,
    message_id: int = 100,
    chat: object = _UNSET,
    from_user: object = _UNSET,
    text: str | None = None,
    photo: object | None = None,
    voice: object | None = None,
    video: object | None = None,
    document: object | None = None,
    sticker: object | None = None,
    animation: object | None = None,
    audio: object | None = None,
    video_note: object | None = None,
    caption: str | None = None,
    reply_to_message: object | None = None,
    media_group_id: str | None = None,
    entities: list | None = None,
    caption_entities: list | None = None,
    is_topic_message: bool = False,
    message_thread_id: int | None = None,
) -> SimpleNamespace:
    user = from_user if from_user is not _UNSET else _make_user(42)
    return SimpleNamespace(
        id=message_id,
        chat=chat if chat is not _UNSET else _make_chat(1, _ChatType.PRIVATE),
        from_user=user,
        sender=user,
        text=text,
        photo=photo,
        voice=voice,
        video=video,
        document=document,
        sticker=sticker,
        animation=animation,
        audio=audio,
        video_note=video_note,
        caption=caption,
        reply_to_message=reply_to_message,
        media_group_id=media_group_id,
        entities=entities or [],
        caption_entities=caption_entities or [],
        is_topic_message=is_topic_message,
        message_thread_id=message_thread_id,
    )


def _make_converter(
    download_path: str | None = "/tmp/file.bin",
    max_download_size_bytes: int = 0,
) -> PyrogramMessageConverter:
    client = SimpleNamespace(download_media=AsyncMock(return_value=download_path))
    return PyrogramMessageConverter(  # type: ignore[arg-type]
        client,
        max_download_size_bytes=max_download_size_bytes,
    )


@pytest.mark.asyncio
async def test_convert_text_private_message() -> None:
    msg = _make_message(text="hello world", chat=_make_chat(1, _ChatType.PRIVATE))
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    assert abm.type == MessageType.FRIEND_MESSAGE
    # 私聊会话以发送者 ID 作为 session_id
    assert abm.session_id == "42"
    assert abm.sender.user_id == "42"
    assert abm.message_str == "hello world"
    assert len(abm.message) == 1
    assert isinstance(abm.message[0], Comp.Plain)
    assert abm.message[0].text == "hello world"


@pytest.mark.asyncio
async def test_convert_text_group_message() -> None:
    msg = _make_message(
        text="hi everyone",
        chat=_make_chat(-100123, _ChatType.SUPERGROUP),
    )
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    assert abm.type == MessageType.GROUP_MESSAGE
    assert abm.group_id == "-100123"
    assert abm.session_id == "-100123"


@pytest.mark.asyncio
async def test_convert_topic_group_message() -> None:
    msg = _make_message(
        text="topic msg",
        chat=_make_chat(-100123, _ChatType.SUPERGROUP),
        is_topic_message=True,
        message_thread_id=77,
    )
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    assert abm.group_id == "-100123#77"
    assert abm.session_id == "-100123#77"


@pytest.mark.asyncio
async def test_convert_strips_bot_username_suffix() -> None:
    msg = _make_message(
        text="/echo@mybot hello",
        chat=_make_chat(1, _ChatType.PRIVATE),
    )
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    # /echo@mybot 应被规范化为 /echo
    assert abm.message_str == "/echo hello"


@pytest.mark.asyncio
async def test_convert_reply_to_bot_in_group_prepends_mention() -> None:
    bot_user = _make_user(99, "mybot")
    reply = _make_message(
        message_id=10,
        from_user=bot_user,
        text="prev",
        chat=_make_chat(-100, _ChatType.SUPERGROUP),
    )
    msg = _make_message(
        message_id=11,
        text="follow-up",
        chat=_make_chat(-100, _ChatType.SUPERGROUP),
        reply_to_message=reply,
    )
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    # 群聊回复 Bot 时会先加上 "/@bot " 前缀以强制唤醒，随后该后缀被规范化移除，
    # 留下 "/" 前缀，AstrBot 去掉 wake_prefix 后得到原始文本 "follow-up"。
    assert abm.message_str == "/ follow-up"


@pytest.mark.asyncio
async def test_convert_photo_downloads_media() -> None:
    photo = SimpleNamespace()
    msg = _make_message(text=None, photo=photo, caption="nice pic")
    converter = _make_converter(download_path="/tmp/photo.jpg")
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    images = [c for c in abm.message if isinstance(c, Comp.Image)]
    assert len(images) == 1
    assert images[0].file == "/tmp/photo.jpg"
    # caption 也应进入消息链
    plains = [c for c in abm.message if isinstance(c, Comp.Plain)]
    assert any(p.text == "nice pic" for p in plains)
    assert abm.message_str == "nice pic"


@pytest.mark.asyncio
async def test_convert_voice_creates_record() -> None:
    voice = SimpleNamespace()
    msg = _make_message(text=None, voice=voice)
    converter = _make_converter(download_path="/tmp/voice.ogg")
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    records = [c for c in abm.message if isinstance(c, Comp.Record)]
    assert len(records) == 1
    assert records[0].file == "/tmp/voice.ogg"


@pytest.mark.asyncio
async def test_convert_document_creates_file_component() -> None:
    document = SimpleNamespace(file_name="readme.txt")
    msg = _make_message(text=None, document=document)
    converter = _make_converter(download_path="/tmp/readme.txt")
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    files = [c for c in abm.message if isinstance(c, Comp.File)]
    assert len(files) == 1
    assert files[0].name == "readme.txt"
    # File.file 会在路径不存在时尝试下载并返回空串，因此断言原始路径 file_
    assert files[0].file_ == "/tmp/readme.txt"
    assert files[0].url == "/tmp/readme.txt"


@pytest.mark.asyncio
async def test_convert_sticker_with_emoji() -> None:
    sticker = SimpleNamespace(emoji="😀")
    msg = _make_message(text=None, sticker=sticker)
    converter = _make_converter(download_path="/tmp/sticker.webp")
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    images = [c for c in abm.message if isinstance(c, Comp.Image)]
    plains = [c for c in abm.message if isinstance(c, Comp.Plain)]
    assert len(images) == 1
    assert any("Sticker:" in p.text for p in plains)


@pytest.mark.asyncio
async def test_convert_mention_entity_becomes_at_component() -> None:
    class _EntityType(str, Enum):
        MENTION = "MENTION"

    # Telegram 的 mention entity 的 offset 指向 "@"，length 包含 "@"
    entity = SimpleNamespace(type=_EntityType.MENTION, offset=4, length=4)  # "@bob"
    msg = _make_message(text="hey @bob there", entities=[entity])
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    ats = [c for c in abm.message if isinstance(c, Comp.At)]
    assert any(getattr(a, "name", None) == "bob" for a in ats)


@pytest.mark.asyncio
async def test_convert_bot_mention_normalizes_case() -> None:
    class _EntityType(str, Enum):
        MENTION = "MENTION"

    # 用户输入的大小写与 Bot 规范用户名不一致（@MyBot -> MyBot）
    entity = SimpleNamespace(type=_EntityType.MENTION, offset=0, length=6)
    msg = _make_message(text="@MyBot hello", entities=[entity])
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    ats = [c for c in abm.message if isinstance(c, Comp.At)]
    assert any(a.qq == "mybot" and a.name == "mybot" for a in ats)


@pytest.mark.asyncio
async def test_convert_bot_mention_keeps_other_case() -> None:
    class _EntityType(str, Enum):
        MENTION = "MENTION"

    # 非 Bot 的提及不应被改写
    entity = SimpleNamespace(type=_EntityType.MENTION, offset=0, length=6)  # "@Alice"
    msg = _make_message(text="@Alice hi", entities=[entity])
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    ats = [c for c in abm.message if isinstance(c, Comp.At)]
    assert any(a.qq == "Alice" for a in ats)


@pytest.mark.asyncio
async def test_convert_returns_none_when_no_from_user() -> None:
    msg = _make_message(text="x", from_user=None)
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is None


@pytest.mark.asyncio
async def test_convert_returns_none_when_no_chat() -> None:
    msg = _make_message(text="x", chat=None)
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is None


@pytest.mark.asyncio
async def test_convert_reply_chain_included() -> None:
    reply = _make_message(message_id=5, text="previous")
    msg = _make_message(message_id=6, text="now", reply_to_message=reply)
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    replies = [c for c in abm.message if isinstance(c, Comp.Reply)]
    assert len(replies) == 1
    assert replies[0].id == "5"


@pytest.mark.asyncio
async def test_convert_oversized_document_is_skipped() -> None:
    """超过下载上限的文档应被跳过：不会调用 download_media，也不会产出 File 组件。"""
    document = SimpleNamespace(file_name="big.bin", file_size=120 * 1024 * 1024)
    msg = _make_message(text=None, document=document)
    converter = _make_converter(
        download_path="/tmp/big.bin", max_download_size_bytes=50 * 1024 * 1024
    )
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    files = [c for c in abm.message if isinstance(c, Comp.File)]
    assert files == []
    converter.client.download_media.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_convert_undersized_document_is_downloaded() -> None:
    """低于下载上限的文档应正常下载并产生 File 组件。"""
    document = SimpleNamespace(file_name="small.bin", file_size=1 * 1024 * 1024)
    msg = _make_message(text=None, document=document)
    converter = _make_converter(
        download_path="/tmp/small.bin", max_download_size_bytes=50 * 1024 * 1024
    )
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    files = [c for c in abm.message if isinstance(c, Comp.File)]
    assert len(files) == 1
    assert files[0].name == "small.bin"
    assert files[0].file_ == "/tmp/small.bin"


@pytest.mark.asyncio
async def test_convert_unknown_size_with_limit_is_downloaded() -> None:
    """无法获取文件大小时（file_size 缺失），即使设置了上限也应尝试下载（保守行为）。"""
    document = SimpleNamespace(file_name="nosize.bin")
    msg = _make_message(text=None, document=document)
    converter = _make_converter(
        download_path="/tmp/nosize.bin", max_download_size_bytes=50 * 1024 * 1024
    )
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    files = [c for c in abm.message if isinstance(c, Comp.File)]
    assert len(files) == 1


@pytest.mark.asyncio
async def test_convert_oversized_video_is_skipped() -> None:
    """视频也受下载上限约束。"""
    video = SimpleNamespace(file_size=80 * 1024 * 1024)
    msg = _make_message(text=None, video=video)
    converter = _make_converter(
        download_path="/tmp/v.mp4", max_download_size_bytes=50 * 1024 * 1024
    )
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    videos = [c for c in abm.message if isinstance(c, Comp.Video)]
    assert videos == []
    converter.client.download_media.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_convert_unlimited_when_zero() -> None:
    """max_download_size_bytes=0 表示不限制，大文件也照样下载。"""
    document = SimpleNamespace(file_name="huge.bin", file_size=10 * 1024 * 1024 * 1024)
    msg = _make_message(text=None, document=document)
    converter = _make_converter(
        download_path="/tmp/huge.bin", max_download_size_bytes=0
    )
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    files = [c for c in abm.message if isinstance(c, Comp.File)]
    assert len(files) == 1


# --------------------------------------------------------------------------- #
# 指向其他机器人的命令过滤
# --------------------------------------------------------------------------- #


def test_is_command_for_other_bot() -> None:
    f = PyrogramMessageConverter.is_command_for_other_bot
    # 指向其他机器人
    assert f("/start@other_bot", bot_usernames=("mybot",)) is True
    assert f("/start@OTHER_bot arg", bot_usernames=("mybot",)) is True
    assert f("/start@other_bot a b c", bot_usernames=("mybot",)) is True
    # 指向本 Bot 或无后缀：正常处理（大小写不敏感）
    assert f("/start", bot_usernames=("mybot",)) is False
    assert f("/start@mybot", bot_usernames=("mybot",)) is False
    assert f("/start@MYBOT hello", bot_usernames=("mybot",)) is False
    # 非命令文本
    assert f("hello /cmd@other", bot_usernames=("mybot",)) is False
    assert f("", bot_usernames=("mybot",)) is False
    # 无自身用户名时保守放行
    assert f("/start@other", bot_usernames=()) is False


def test_is_command_for_other_bot_with_multiple_usernames() -> None:
    """Bot 拥有多个用户名时，指向任一用户名的命令都应视为发给本 Bot。"""
    f = PyrogramMessageConverter.is_command_for_other_bot
    usernames = ("mybot", "mybot_alt")
    assert f("/start@mybot_alt", bot_usernames=usernames) is False
    assert f("/start@MYBOT_ALT hello", bot_usernames=usernames) is False
    assert f("/start@mybot", bot_usernames=usernames) is False
    assert f("/start@other_bot", bot_usernames=usernames) is True


@pytest.mark.asyncio
async def test_convert_ignores_command_for_other_bot() -> None:
    """/cmd@其他Bot 不应被转换为 AstrBotMessage。"""
    msg = _make_message(
        text="/start@other_bot",
        chat=_make_chat(-100123, _ChatType.SUPERGROUP),
    )
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is None


@pytest.mark.asyncio
async def test_convert_allows_command_for_self_and_bare() -> None:
    """/cmd@本Bot 与 /cmd 均应正常通过。"""
    converter = _make_converter()
    for text, expected in (
        ("/echo@mybot hi", "/echo hi"),
        ("/echo", "/echo"),
    ):
        msg = _make_message(
            text=text,
            chat=_make_chat(-100123, _ChatType.SUPERGROUP),
        )
        abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
        assert abm is not None
        assert abm.message_str == expected


@pytest.mark.asyncio
async def test_convert_caption_command_for_other_bot_is_skipped() -> None:
    """指向其他机器人的命令式 caption 不应写入消息链。"""
    photo = SimpleNamespace(file_size=100)
    msg = _make_message(text=None, photo=photo, caption="/help@other_bot")
    converter = _make_converter()
    abm = await converter.convert(msg, bot_usernames=("mybot",), bot_id=99)
    assert abm is not None
    assert abm.message_str == ""
    assert all(not isinstance(c, Comp.Plain) for c in abm.message)


# --------------------------------------------------------------------------- #
# 多用户名（collectible usernames）支持
# --------------------------------------------------------------------------- #


def test_collect_bot_usernames_from_username_and_usernames() -> None:
    """主用户名与附加用户名应合并去重，主用户名在前。"""
    user = SimpleNamespace(
        username="mybot",
        usernames=[
            SimpleNamespace(username="mybot_alt", active=True),
            SimpleNamespace(username="mybot_collect", active=True),
        ],
    )
    assert collect_bot_usernames(user) == ("mybot", "mybot_alt", "mybot_collect")


def test_collect_bot_usernames_handles_plain_strings() -> None:
    """usernames 元素为纯字符串时也应被识别。"""
    user = SimpleNamespace(username="mybot", usernames=["mybot_alt"])
    assert collect_bot_usernames(user) == ("mybot", "mybot_alt")


def test_collect_bot_usernames_empty_list() -> None:
    """usernames 为空列表时只保留主用户名。"""
    user = SimpleNamespace(username="mybot", usernames=[])
    assert collect_bot_usernames(user) == ("mybot",)


def test_collect_bot_usernames_missing_usernames_attribute() -> None:
    """usernames 属性缺失（None）时不应报错。"""
    user = SimpleNamespace(username="mybot", usernames=None)
    assert collect_bot_usernames(user) == ("mybot",)


def test_collect_bot_usernames_skips_inactive() -> None:
    """非活跃（inactive）的可收集用户名不应用于提及匹配。"""
    user = SimpleNamespace(
        username="mybot",
        usernames=[SimpleNamespace(username="mybot_off", active=False)],
    )
    assert collect_bot_usernames(user) == ("mybot",)


def test_collect_bot_usernames_deduplicates_case_insensitively() -> None:
    """与主用户名重复的附加用户名应被去重。"""
    user = SimpleNamespace(
        username="mybot",
        usernames=[SimpleNamespace(username="MyBot", active=True)],
    )
    assert collect_bot_usernames(user) == ("mybot",)


def test_collect_bot_usernames_without_any_username() -> None:
    """既无主用户名也无附加用户名时返回空元组。"""
    user = SimpleNamespace(username=None, usernames=None)
    assert collect_bot_usernames(user) == ()


@pytest.mark.asyncio
async def test_convert_strips_secondary_username_suffix() -> None:
    """使用附加用户名调用的指令应被规范化为纯指令。"""
    msg = _make_message(
        text="/echo@mybot_alt hello",
        chat=_make_chat(-100123, _ChatType.SUPERGROUP),
    )
    converter = _make_converter()
    abm = await converter.convert(
        msg, bot_usernames=("mybot", "mybot_alt"), bot_id=99
    )
    assert abm is not None
    assert abm.message_str == "/echo hello"


@pytest.mark.asyncio
async def test_convert_secondary_username_mention_normalized_to_primary() -> None:
    """以附加用户名 @ Bot 时，提及应规范为主用户名以匹配 self_id。"""
    class _EntityType(str, Enum):
        MENTION = "MENTION"

    entity = SimpleNamespace(type=_EntityType.MENTION, offset=0, length=10)  # "@mybot_alt"
    msg = _make_message(text="@mybot_alt hello", entities=[entity])
    converter = _make_converter()
    abm = await converter.convert(
        msg, bot_usernames=("mybot", "mybot_alt"), bot_id=99
    )
    assert abm is not None
    assert abm.self_id == "mybot"
    ats = [c for c in abm.message if isinstance(c, Comp.At)]
    assert any(a.qq == "mybot" and a.name == "mybot" for a in ats)


@pytest.mark.asyncio
async def test_convert_uses_primary_username_as_self_id() -> None:
    """self_id 始终为主用户名，保证 AstrBot 唤醒检查可匹配。"""
    msg = _make_message(text="hi", chat=_make_chat(1, _ChatType.PRIVATE))
    converter = _make_converter()
    abm = await converter.convert(
        msg, bot_usernames=("mybot", "mybot_alt"), bot_id=99
    )
    assert abm is not None
    assert abm.self_id == "mybot"


@pytest.mark.asyncio
async def test_convert_reply_to_bot_prepends_primary_username() -> None:
    """群聊中回复 Bot 时应加上主用户名的 /@ 前缀。"""
    bot_user = _make_user(99, "mybot")
    reply = _make_message(
        message_id=10,
        from_user=bot_user,
        text="prev",
        chat=_make_chat(-100, _ChatType.SUPERGROUP),
    )
    msg = _make_message(
        message_id=11,
        text="follow-up",
        chat=_make_chat(-100, _ChatType.SUPERGROUP),
        reply_to_message=reply,
    )
    converter = _make_converter()
    abm = await converter.convert(
        msg, bot_usernames=("mybot", "mybot_alt"), bot_id=99
    )
    assert abm is not None
    # 与单用户名场景一致："/@主用户名 " 前缀会被规范化为 "/" 触发唤醒
    assert abm.message_str == "/ follow-up"
