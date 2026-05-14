# astrbot_plugin_pyrogram_adapter

> 基于 [kurigram](https://github.com/KurimuzonAkuma/pyrogram)（Pyrogram 维护分叉）的
> AstrBot Telegram **Bot** 平台适配器。仅支持 Bot 模式，不支持 Userbot。

本插件以 AstrBot Star 形式分发，加载时通过 `@register_platform_adapter` 装饰器
向 AstrBot 平台注册表注入名为 **`pyrogram_bot`** 的适配器。它与官方使用
`python-telegram-bot` 实现的 `telegram` 适配器、以及社区使用 `telethon` 实现的
`telethon_userbot` 适配器可以同时安装、互不冲突。

## 特性一览

- ✅ **消息接收**：文本、图片、贴纸、动画 (GIF)、视频、视频备注、语音、音频、文档
- ✅ **媒体组聚合**：相册式多媒体消息会去抖合并为单条 `AstrBotMessage`
- ✅ **消息发送**：文本（Markdown，失败回退纯文本）、图片、GIF、文件、语音、视频
- ✅ **流式输出**：`send_streaming` 使用 `send_message` + `edit_message_text` 实现 token 级更新
- ✅ **消息反应**：`event.react(emoji)` 调用 Telegram 的 `sendReaction`
- ✅ **指令注册**：自动收集 AstrBot 中声明的指令并周期性同步到 Telegram BotFather
- ✅ **超级群话题**：`session_id` 形式为 `chat_id#thread_id`，按 topic 隔离会话
- ✅ **/start 欢迎语**：可配置的 `start_message`
- ✅ **私聊与群聊**：与官方 telegram 适配器一致的 `MessageType` 行为

## 安装

将整个目录置于 AstrBot 的插件目录（默认是 `<AstrBot 数据目录>/plugins/`）下，
或直接克隆本仓库到该位置。AstrBot 启动时会自动加载。

依赖：

```text
kurigram>=2.2.0
```

如使用 `uv`：

```bash
uv pip install kurigram
```

## 配置项

在 AstrBot WebUI → 平台管理 → 添加平台，选择 **`pyrogram_bot`**，填写：

| 字段 | 说明 |
| --- | --- |
| `id` | 适配器实例 ID（唯一） |
| `api_id` | 从 <https://my.telegram.org> 获取的 API ID |
| `api_hash` | 从 <https://my.telegram.org> 获取的 API Hash |
| `bot_token` | BotFather 颁发的 Token，形如 `123456:ABCDEF...` |
| `in_memory` | `true` 时不写 session 文件（推荐） |
| `workdir` | 仅当 `in_memory=false` 时，指定 session 存放目录 |
| `start_message` | 用户发送 `/start` 时机器人的欢迎语 |
| `pyrogram_command_register` | 是否自动注册指令到 Telegram |
| `pyrogram_command_auto_refresh` | 是否周期性刷新指令 |
| `pyrogram_command_register_interval` | 刷新间隔（秒，最小 10） |
| `pyrogram_media_group_timeout` | 媒体组去抖窗口（秒） |
| `pyrogram_media_group_max_wait` | 媒体组等待上限（秒） |
| `pyrogram_streaming_throttle` | 流式输出最小编辑间隔（秒） |

## 架构与设计

```
astrbot_plugin_pyrogram_adapter/
├── main.py                       # AstrBot Star 入口（壳插件）
├── plugin_info.py                # 插件元信息
├── metadata.yaml                 # AstrBot 插件元数据
├── requirements.txt              # Python 依赖
├── pyrogram_adapter/             # 适配器实现包
│   ├── __init__.py               # 导出 PyrogramPlatformAdapter
│   ├── config.py                 # 配置模板/校验/解析
│   ├── message_converter.py      # kurigram Message → AstrBotMessage
│   ├── pyrogram_event.py         # PlatformEvent 实现（发送/流式/反应）
│   └── pyrogram_adapter.py       # PlatformAdapter 主体（生命周期/分发/指令）
└── tests/                        # 单元测试（pytest + pytest-asyncio）
```

### 关键设计决策

1. **仅支持 Bot 模式**：与 BotFather Token 绑定，不实现 Userbot 私聊扫描、prune 等需要
   用户账号的能力。所有逻辑围绕 `bot_token + api_id + api_hash` 三件套展开。
2. **复用 AstrBot 官方组件**：`AstrBotMessage`、`AstrMessageEvent`、`MessageChain`、
   `register_platform_adapter` 全部来自 AstrBot 主仓库，确保 100% 兼容。
3. **媒体组聚合**：和官方 telegram 适配器一致，使用 APScheduler 的延迟任务做去抖；
   超过 `pyrogram_media_group_max_wait` 后立即触发。
4. **流式输出**：私聊与群聊统一采用 `send_message` + `edit_message_text` 方案。这与
   官方 telegram 适配器的群聊 fallback 一致，避免依赖 `sendMessageDraft` 这一 kurigram
   尚未完全暴露的私有 API。
5. **Markdown + 回退**：所有正式文本以 Pyrogram 传统 Markdown 模式（`ParseMode.MARKDOWN`）
   发送，无需对 `!`、`.`、`-` 等普通字符做反斜杠转义；任何异常（未闭合语法、特殊字符）都
   回退到纯文本（`ParseMode.DISABLED`），保证消息一定能送达。
6. **临时文件**：媒体下载到 AstrBot 的临时目录（`astrbot.core.utils.astrbot_path.get_astrbot_temp_path()`），
   清理由 AstrBot 主进程负责。

### 与其他适配器的关系

| 适配器 | 底层库 | 场景 | 与本适配器关系 |
| --- | --- | --- | --- |
| `telegram`（官方） | `python-telegram-bot` | Bot | 同类，可同时存在；本适配器侧重 MTProto 协议 |
| `telethon_userbot`（社区） | `telethon` | Userbot | 完全不同的账号类型 |
| **`pyrogram_bot`（本插件）** | `kurigram` | Bot | — |

## 使用示例

启动 AstrBot 后，给机器人发送 `/start` 或任意消息即可。所有 AstrBot 插件
（如 LLM 对话、命令、知识库等）都会通过 `pyrogram_bot` 适配器响应。

### 调用消息反应

在你的 Star 插件中：

```python
@filter.command("ping")
async def ping(self, event: AstrMessageEvent):
    await event.react("👍")
    yield event.plain_result("pong")
```

仅在通过 `pyrogram_bot` 适配器到达时，`react()` 会真正生效；其它平台默认为 no-op。

### 流式输出

只要 LLM 提供商支持流式响应，本适配器即可自动以 token 级别更新消息。

## 测试

测试位于 `tests/` 目录，使用 `pytest + pytest-asyncio`，全程通过 mock 模拟
`kurigram` 的 `Client` 与 `Message` 对象，因此本机无需 Telegram 凭证即可运行。

```bash
cd astrbot_plugin_pyrogram_adapter
pytest -v
```

测试覆盖：

- `tests/test_config.py`：配置解析与校验
- `tests/test_message_converter.py`：消息转换（文本、媒体、回复、提及、超级群话题）
- `tests/test_pyrogram_event.py`：文本切分、chat action 推断、发送、流式、反应
- `tests/test_pyrogram_adapter.py`：平台注册、媒体组聚合、指令收集、`send_by_session`

## 许可证

MIT License，详见 [LICENSE](LICENSE)。
