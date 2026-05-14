"""tests/test_message_converter.py - PyrogramMessageConverter 测试。"""
from __future__ import annotations

import os
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import astrbot.api.message_components as Comp
from astrbot.api.platform import MessageType
from pyrogram_adapter.message_converter import PyrogramMessageConverter


class _ChatType(str, Enum):
    PRIVATE = "PRIVATE"
    GROUP = "GROUP"
    SUPERGROUP = "SUPERGROUP"


def _make_user(user_id: int, username: str = "alice") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=username, first_name=username)


def _make_chat(chat_id: int, chat_type: _ChatType = _ChatType.PRIVATE) -> SimpleNamespace:
    return SimpleNamespace(id=chat_id, type=chat_type)


def _make_message(
    *,
    message_id: int = 100,
    chat: SimpleNamespace | None = None,
    from_user: SimpleNamespace | None = None,
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
    return SimpleNamespace(
        id=message_id,
        chat=chat or _make_chat(1, _ChatType.PRIVATE),
        from_user=from_user or _make_user(42),
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


def _make_converter(download_path: str | None = "/tmp/file.bin") -> PyrogramMessageConverter:
    client = SimpleNamespace(download_media=AsyncMock(return_value=download_path))
    return PyrogramMessageConverter(client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_convert_text_private_message() -> None:
    msg = _make_message(text="hello world", chat=_make_chat(1, _ChatType.PRIVATE))
    converter = _make_converter()
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    assert abm.type == MessageType.FRIEND_MESSAGE
    assert abm.session_id == "1"
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
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
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
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
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
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
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
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    # 群聊回复 Bot 时应该加上 /@bot 前缀
    assert abm.message_str.startswith("/@mybot ")


@pytest.mark.asyncio
async def test_convert_photo_downloads_media() -> None:
    photo = SimpleNamespace()
    msg = _make_message(text=None, photo=photo, caption="nice pic")
    converter = _make_converter(download_path="/tmp/photo.jpg")
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
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
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    records = [c for c in abm.message if isinstance(c, Comp.Record)]
    assert len(records) == 1
    assert records[0].file == "/tmp/voice.ogg"


@pytest.mark.asyncio
async def test_convert_document_creates_file_component() -> None:
    document = SimpleNamespace(file_name="readme.txt")
    msg = _make_message(text=None, document=document)
    converter = _make_converter(download_path="/tmp/readme.txt")
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    files = [c for c in abm.message if isinstance(c, Comp.File)]
    assert len(files) == 1
    assert files[0].name == "readme.txt"
    assert files[0].file == "/tmp/readme.txt"


@pytest.mark.asyncio
async def test_convert_sticker_with_emoji() -> None:
    sticker = SimpleNamespace(emoji="😀")
    msg = _make_message(text=None, sticker=sticker)
    converter = _make_converter(download_path="/tmp/sticker.webp")
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    images = [c for c in abm.message if isinstance(c, Comp.Image)]
    plains = [c for c in abm.message if isinstance(c, Comp.Plain)]
    assert len(images) == 1
    assert any("Sticker:" in p.text for p in plains)


@pytest.mark.asyncio
async def test_convert_mention_entity_becomes_at_component() -> None:
    class _EntityType(str, Enum):
        MENTION = "MENTION"

    entity = SimpleNamespace(type=_EntityType.MENTION, offset=5, length=4)  # "@bob"
    msg = _make_message(text="hey @bob there", entities=[entity])
    converter = _make_converter()
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    ats = [c for c in abm.message if isinstance(c, Comp.At)]
    assert any(getattr(a, "name", None) == "bob" for a in ats)


@pytest.mark.asyncio
async def test_convert_returns_none_when_no_from_user() -> None:
    msg = _make_message(text="x", from_user=None)
    converter = _make_converter()
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is None


@pytest.mark.asyncio
async def test_convert_returns_none_when_no_chat() -> None:
    msg = _make_message(text="x", chat=None)
    converter = _make_converter()
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is None


@pytest.mark.asyncio
async def test_convert_reply_chain_included() -> None:
    reply = _make_message(message_id=5, text="previous")
    msg = _make_message(message_id=6, text="now", reply_to_message=reply)
    converter = _make_converter()
    abm = await converter.convert(msg, bot_username="mybot", bot_id=99)
    assert abm is not None
    replies = [c for c in abm.message if isinstance(c, Comp.Reply)]
    assert len(replies) == 1
    assert replies[0].id == "5"
