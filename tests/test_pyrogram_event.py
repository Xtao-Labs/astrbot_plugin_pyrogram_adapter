"""tests/test_pyrogram_event.py - 测试发送、流式、反应等核心能力。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, PlatformMetadata
from pyrogram_adapter.pyrogram_event import PyrogramPlatformEvent


def _make_event(
    *,
    is_group: bool = False,
    session_id: str = "100",
    group_id: str | None = None,
    client: Any | None = None,
) -> PyrogramPlatformEvent:
    abm = AstrBotMessage()
    abm.session_id = session_id
    abm.message_id = "999"
    abm.sender = MessageMember(user_id=session_id, nickname="tester")
    abm.self_id = "bot"
    abm.message = []
    abm.message_str = ""
    abm.type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
    if is_group:
        abm.group_id = group_id or session_id

    meta = PlatformMetadata(name="pyrogram_bot", description="x", id="pyrogram_bot")
    event = PyrogramPlatformEvent(
        message_str="",
        message_obj=abm,
        platform_meta=meta,
        session_id=session_id,
        client=client or MagicMock(),
    )
    return event


# ---------------------------------------------------------------------------
# 文本切分
# ---------------------------------------------------------------------------
class TestSplitMessage:
    def test_short_text_returns_single_chunk(self) -> None:
        chunks = PyrogramPlatformEvent._split_message("hello")
        assert chunks == ["hello"]

    def test_long_text_split_by_length(self) -> None:
        text = "a" * (PyrogramPlatformEvent.MAX_MESSAGE_LENGTH + 100)
        chunks = PyrogramPlatformEvent._split_message(text)
        assert len(chunks) == 2
        assert all(len(c) <= PyrogramPlatformEvent.MAX_MESSAGE_LENGTH for c in chunks)

    def test_long_text_prefers_paragraph_break(self) -> None:
        line = "x" * 1000
        text = (line + "\n\n") * 5  # > 4096 chars, contains paragraph boundaries
        chunks = PyrogramPlatformEvent._split_message(text)
        for chunk in chunks:
            assert len(chunk) <= PyrogramPlatformEvent.MAX_MESSAGE_LENGTH


# ---------------------------------------------------------------------------
# session_id 拆解
# ---------------------------------------------------------------------------
class TestSplitSessionId:
    def test_plain_chat_id(self) -> None:
        chat_id, thread_id = PyrogramPlatformEvent._split_session_id("123")
        assert chat_id == 123
        assert thread_id is None

    def test_topic_chat_id(self) -> None:
        chat_id, thread_id = PyrogramPlatformEvent._split_session_id("-100123#77")
        assert chat_id == -100123
        assert thread_id == 77

    def test_invalid_session_id_raises(self) -> None:
        with pytest.raises(ValueError):
            PyrogramPlatformEvent._split_session_id("not_a_number")


# ---------------------------------------------------------------------------
# chat action 选择
# ---------------------------------------------------------------------------
class TestChatActionMapping:
    def test_image_picks_upload_photo(self) -> None:
        chain = [Comp.Image(file="x", url="x")]
        assert (
            PyrogramPlatformEvent._get_chat_action_for_chain(chain)
            == PyrogramPlatformEvent.ACTION_UPLOAD_PHOTO
        )

    def test_video_picks_upload_video(self) -> None:
        chain = [Comp.Video(file="x", path="x")]
        assert (
            PyrogramPlatformEvent._get_chat_action_for_chain(chain)
            == PyrogramPlatformEvent.ACTION_UPLOAD_VIDEO
        )

    def test_plain_text_picks_typing(self) -> None:
        chain = [Comp.Plain("hi")]
        assert (
            PyrogramPlatformEvent._get_chat_action_for_chain(chain)
            == PyrogramPlatformEvent.ACTION_TYPING
        )

    def test_empty_chain_falls_back_to_typing(self) -> None:
        assert (
            PyrogramPlatformEvent._get_chat_action_for_chain([])
            == PyrogramPlatformEvent.ACTION_TYPING
        )


# ---------------------------------------------------------------------------
# send_with_client：覆盖文本和媒体的发送路径
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_with_client_sends_plain_text() -> None:
    client = SimpleNamespace(
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    chain = MessageChain(chain=[Comp.Plain("hello")])
    await PyrogramPlatformEvent.send_with_client(client, chain, "100")
    assert client.send_chat_action.await_count == 1
    assert client.send_message.await_count >= 1
    call_kwargs = client.send_message.await_args.kwargs
    assert call_kwargs.get("chat_id") == 100


@pytest.mark.asyncio
async def test_send_with_client_handles_reply() -> None:
    client = SimpleNamespace(
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    chain = MessageChain(
        chain=[Comp.Reply(id="55", chain=[]), Comp.Plain("yo")]
    )
    await PyrogramPlatformEvent.send_with_client(client, chain, "100")
    call_kwargs = client.send_message.await_args.kwargs
    assert call_kwargs.get("reply_to_message_id") == 55


@pytest.mark.asyncio
async def test_send_with_client_topic_thread() -> None:
    client = SimpleNamespace(
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    chain = MessageChain(chain=[Comp.Plain("yo")])
    await PyrogramPlatformEvent.send_with_client(client, chain, "-100123#77")
    call_kwargs = client.send_message.await_args.kwargs
    assert call_kwargs.get("chat_id") == -100123
    assert call_kwargs.get("message_thread_id") == 77


# ---------------------------------------------------------------------------
# react: 应调用 client.send_reaction
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_react_calls_send_reaction() -> None:
    client = SimpleNamespace(send_reaction=AsyncMock())
    event = _make_event(client=client, session_id="100")
    await event.react("👍")
    assert client.send_reaction.await_count == 1
    kwargs = client.send_reaction.await_args.kwargs
    assert kwargs["chat_id"] == 100
    assert kwargs["message_id"] == 999
    assert kwargs["emoji"] == "👍"


@pytest.mark.asyncio
async def test_react_with_empty_emoji_clears_reaction() -> None:
    client = SimpleNamespace(send_reaction=AsyncMock())
    event = _make_event(client=client, session_id="100")
    await event.react(None)
    kwargs = client.send_reaction.await_args.kwargs
    assert kwargs["emoji"] is None


# ---------------------------------------------------------------------------
# streaming：能够 fall through 并产生 send_message + edit_message_text
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_streaming_basic_flow() -> None:
    sent = SimpleNamespace(id=12345)
    client = SimpleNamespace(
        send_message=AsyncMock(return_value=sent),
        edit_message_text=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    event = _make_event(client=client, session_id="100")
    event.streaming_throttle = 0.0  # 确保每段都触发 edit

    async def gen():
        yield MessageChain(chain=[Comp.Plain("Hello")])
        yield MessageChain(chain=[Comp.Plain(" world")])

    await event.send_streaming(gen())
    assert client.send_message.await_count == 1
    assert client.edit_message_text.await_count >= 1


@pytest.mark.asyncio
async def test_send_streaming_break_resets_state() -> None:
    sent = SimpleNamespace(id=12345)
    client = SimpleNamespace(
        send_message=AsyncMock(return_value=sent),
        edit_message_text=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    event = _make_event(client=client, session_id="100")
    event.streaming_throttle = 0.0

    async def gen():
        yield MessageChain(chain=[Comp.Plain("first")])
        yield MessageChain(type="break", chain=[])
        yield MessageChain(chain=[Comp.Plain("second")])

    await event.send_streaming(gen())
    # break 后应再次 send_message（新一条）
    assert client.send_message.await_count == 2
