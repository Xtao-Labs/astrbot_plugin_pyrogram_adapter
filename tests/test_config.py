"""tests/test_config.py - PyrogramAdapterConfig 的单元测试。"""
from __future__ import annotations

import pytest

from pyrogram_adapter.config import (
    DEFAULT_CONFIG_TEMPLATE,
    PyrogramAdapterConfig,
    parse_bool,
    parse_float,
    parse_int,
    parse_str,
)


VALID_CONFIG = {
    "id": "my_bot",
    "api_id": 123456,
    "api_hash": "abcd1234efgh",
    "bot_token": "11111:AABBCCDDEEFF",
    "in_memory": True,
    "start_message": "hi there",
    "pyrogram_command_register": True,
    "pyrogram_command_auto_refresh": False,
    "pyrogram_command_register_interval": 200,
    "pyrogram_media_group_timeout": 1.5,
    "pyrogram_media_group_max_wait": 6.0,
    "pyrogram_streaming_throttle": 0.3,
    "pyrogram_max_download_size_mb": 100,
}


class TestParsers:
    def test_parse_bool_truthy(self) -> None:
        assert parse_bool("true", False) is True
        assert parse_bool("YES", False) is True
        assert parse_bool(1, False) is True

    def test_parse_bool_falsy(self) -> None:
        assert parse_bool("0", True) is False
        assert parse_bool("no", True) is False
        assert parse_bool(0, True) is False

    def test_parse_bool_default(self) -> None:
        assert parse_bool(None, True) is True
        assert parse_bool("", True) is True
        assert parse_bool("not_a_bool", True) is True

    def test_parse_int(self) -> None:
        assert parse_int("42", 0) == 42
        assert parse_int("not_int", 7) == 7
        assert parse_int(None, 7) == 7
        assert parse_int(3.7, 0) == 3

    def test_parse_float(self) -> None:
        assert parse_float("1.5", 0) == 1.5
        assert parse_float("bad", 2.0) == 2.0

    def test_parse_str(self) -> None:
        assert parse_str("  hello  ", "x") == "hello"
        assert parse_str("", "fallback") == "fallback"
        assert parse_str(None, "fallback") == "fallback"


class TestPyrogramAdapterConfig:
    def test_from_dict_valid(self) -> None:
        cfg = PyrogramAdapterConfig.from_dict(VALID_CONFIG)
        assert cfg.adapter_id == "my_bot"
        assert cfg.api_id == 123456
        assert cfg.api_hash == "abcd1234efgh"
        assert cfg.bot_token == "11111:AABBCCDDEEFF"
        assert cfg.in_memory is True
        assert cfg.command_register is True
        assert cfg.command_auto_refresh is False
        assert cfg.command_register_interval == 200
        assert cfg.media_group_timeout == 1.5
        assert cfg.media_group_max_wait == 6.0
        assert cfg.streaming_throttle == 0.3
        assert cfg.max_download_size_mb == 100
        assert cfg.max_download_size_bytes == 100 * 1024 * 1024

    def test_missing_api_id(self) -> None:
        raw = dict(VALID_CONFIG, api_id=0)
        with pytest.raises(ValueError, match="api_id"):
            PyrogramAdapterConfig.from_dict(raw)

    def test_missing_api_hash(self) -> None:
        raw = dict(VALID_CONFIG, api_hash="")
        with pytest.raises(ValueError, match="api_hash"):
            PyrogramAdapterConfig.from_dict(raw)

    def test_bad_bot_token(self) -> None:
        raw = dict(VALID_CONFIG, bot_token="invalid_no_colon")
        with pytest.raises(ValueError, match="bot_token"):
            PyrogramAdapterConfig.from_dict(raw)

    def test_zero_media_group_max_wait(self) -> None:
        raw = dict(VALID_CONFIG, pyrogram_media_group_max_wait=0)
        # 由于 parse_float 会把 0 钳制为 0.1，因此校验通过；这里验证逻辑一致
        cfg = PyrogramAdapterConfig.from_dict(raw)
        assert cfg.media_group_max_wait >= 0.1

    def test_default_template_keys_match(self) -> None:
        # 确保默认模板没有遗漏配置项
        expected_keys = {
            "id",
            "api_id",
            "api_hash",
            "bot_token",
            "in_memory",
            "workdir",
            "start_message",
            "pyrogram_command_register",
            "pyrogram_command_auto_refresh",
            "pyrogram_command_register_interval",
            "pyrogram_media_group_timeout",
            "pyrogram_media_group_max_wait",
            "pyrogram_streaming_throttle",
            "pyrogram_max_download_size_mb",
        }
        assert expected_keys.issubset(set(DEFAULT_CONFIG_TEMPLATE.keys()))

    def test_minimum_interval_floor(self) -> None:
        raw = dict(VALID_CONFIG, pyrogram_command_register_interval=1)
        cfg = PyrogramAdapterConfig.from_dict(raw)
        # 至少 10 秒
        assert cfg.command_register_interval == 10

    def test_default_max_download_size_is_50mb(self) -> None:
        # 未显式设置时应默认为 50 MB
        cfg = PyrogramAdapterConfig.from_dict(VALID_CONFIG)
        cfg2 = PyrogramAdapterConfig.from_dict({})
        assert cfg2.max_download_size_mb == 50
        assert cfg2.max_download_size_bytes == 50 * 1024 * 1024

    def test_max_download_size_zero_means_unlimited(self) -> None:
        raw = dict(VALID_CONFIG, pyrogram_max_download_size_mb=0)
        cfg = PyrogramAdapterConfig.from_dict(raw)
        assert cfg.max_download_size_mb == 0
        assert cfg.max_download_size_bytes == 0

    def test_max_download_size_negative_clamps_to_zero(self) -> None:
        raw = dict(VALID_CONFIG, pyrogram_max_download_size_mb=-10)
        cfg = PyrogramAdapterConfig.from_dict(raw)
        assert cfg.max_download_size_mb == 0
        assert cfg.max_download_size_bytes == 0
