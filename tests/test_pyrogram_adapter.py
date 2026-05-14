"""tests/test_pyrogram_adapter.py - PyrogramPlatformAdapter 的单元测试。

由于 ``@register_platform_adapter`` 是模块加载时立即触发的副作用，本测试
导入 adapter 子模块以确保注册过程不会抛错；其余测试在 mock 掉
kurigram Client 后验证 adapter 的核心逻辑。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.platform.register import platform_cls_map, platform_registry


VALID_CONFIG = {
    "id": "pyrogram_bot_test",
    "api_id": 123456,
    "api_hash": "abcd1234",
    "bot_token": "11111:AABBCC",
    "in_memory": True,
    "pyrogram_command_register": False,
    "pyrogram_command_auto_refresh": False,
    "pyrogram_media_group_timeout": 0.01,
    "pyrogram_media_group_max_wait": 0.1,
}


def _import_adapter_cls():
    """惰性导入并返回 PyrogramPlatformAdapter 类。"""
    from pyrogram_adapter.pyrogram_adapter import PyrogramPlatformAdapter

    return PyrogramPlatformAdapter


def test_adapter_registered_in_platform_registry() -> None:
    _import_adapter_cls()  # 触发注册
    names = [pm.name for pm in platform_registry]
    assert "pyrogram_bot" in names
    assert "pyrogram_bot" in platform_cls_map


def test_meta_returns_expected_metadata() -> None:
    cls = _import_adapter_cls()
    adapter = cls(VALID_CONFIG, {}, asyncio.Queue())
    meta = adapter.meta()
    assert meta.name == "pyrogram_bot"
    assert meta.id == "pyrogram_bot_test"
    assert meta.support_streaming_message is True


def test_collect_commands_returns_list() -> None:
    cls = _import_adapter_cls()
    adapter = cls(VALID_CONFIG, {}, asyncio.Queue())
    commands = adapter.collect_commands()
    assert isinstance(commands, list)
    for cmd in commands:
        assert "command" in cmd
        assert "description" in cmd
        # /start 应该被跳过
        assert cmd["command"] != "start"
        # 命令名规范
        assert len(cmd["command"]) <= 32


@pytest.mark.asyncio
async def test_media_group_aggregation_dispatches_single_event() -> None:
    cls = _import_adapter_cls()
    adapter = cls(VALID_CONFIG, {}, asyncio.Queue())

    # Mock converter，每条消息转换出 1 个 Plain 组件
    import astrbot.api.message_components as Comp
    from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType

    def _make_abm(text: str) -> AstrBotMessage:
        abm = AstrBotMessage()
        abm.session_id = "1"
        abm.message_id = "1"
        abm.sender = MessageMember(user_id="1", nickname="u")
        abm.self_id = "bot"
        abm.message = [Comp.Plain(text)]
        abm.message_str = text
        abm.type = MessageType.FRIEND_MESSAGE
        return abm

    converter = MagicMock()
    converter.convert = AsyncMock(side_effect=lambda msg, **kw: _make_abm(msg.text))
    adapter._converter = converter

    # 准备 3 条同组消息
    msgs = [
        SimpleNamespace(
            id=i,
            chat=SimpleNamespace(id=1),
            text=f"part{i}",
            media_group_id="group_x",
        )
        for i in range(3)
    ]

    # 启动 scheduler 用于测试
    adapter.scheduler.start()
    try:
        for m in msgs:
            await adapter._handle_media_group_message(m)
        # 等待去抖触发
        await asyncio.sleep(0.3)
    finally:
        adapter.scheduler.shutdown(wait=False)

    # 应只有一个事件被 commit
    assert adapter._event_queue.qsize() == 1
    event = await adapter._event_queue.get()
    # 三条消息的 Plain 应当被合并
    plains = [c for c in event.message_obj.message if isinstance(c, Comp.Plain)]
    assert len(plains) == 3


@pytest.mark.asyncio
async def test_send_by_session_delegates_to_event() -> None:
    cls = _import_adapter_cls()
    adapter = cls(VALID_CONFIG, {}, asyncio.Queue())
    adapter.client = SimpleNamespace(
        send_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )

    from astrbot.api.event import MessageChain
    from astrbot.api.platform import MessageType
    from astrbot.core.platform.astr_message_event import MessageSesion
    import astrbot.api.message_components as Comp

    session = MessageSesion(
        platform_name="pyrogram_bot",
        message_type=MessageType.FRIEND_MESSAGE,
        session_id="100",
    )
    chain = MessageChain(chain=[Comp.Plain("hi")])
    await adapter.send_by_session(session, chain)
    assert adapter.client.send_message.await_count == 1


def test_extract_command_info_filters_invalid_names() -> None:
    cls = _import_adapter_cls()
    from astrbot.core.star.filter.command import CommandFilter

    filt = CommandFilter("INVALID-NAME!", alias=None, handler_md=None)
    handler_md = SimpleNamespace(desc="x", enabled=True)
    result = cls._extract_command_info(filt, handler_md, set())
    assert result is None  # 不合法的命令名被过滤


def test_extract_command_info_normal() -> None:
    cls = _import_adapter_cls()
    from astrbot.core.star.filter.command import CommandFilter

    filt = CommandFilter("echo", alias=None, handler_md=None)
    handler_md = SimpleNamespace(desc="echo command", enabled=True)
    result = cls._extract_command_info(filt, handler_md, set())
    assert result == [("echo", "echo command")]
