"""AstrBot 插件入口。

仅做一件事：导入 ``pyrogram_adapter`` 子包以触发 ``@register_platform_adapter``
装饰器把 ``PyrogramPlatformAdapter`` 注册进 AstrBot 平台注册表。
"""

from __future__ import annotations

from astrbot.api.star import Context, Star, register

from .plugin_info import (
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    PLUGIN_NAME,
    PLUGIN_REPO,
    PLUGIN_VERSION,
)


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION, PLUGIN_REPO)
class PyrogramAdapterPlugin(Star):
    """壳插件：仅用于触发平台适配器的注册。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        # 仅在插件加载时导入子包，触发 @register_platform_adapter 装饰器。
        from .pyrogram_adapter import PyrogramPlatformAdapter  # noqa: F401
