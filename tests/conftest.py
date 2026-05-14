"""pytest 配置：把项目根目录加入 sys.path。"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_ROOT = _THIS_DIR.parent
_ASTRBOT_DIR = _ROOT.parent / "AstrBot"

# 让测试能直接 import astrbot_plugin_pyrogram_adapter.*
for path in (_ROOT, _ASTRBOT_DIR):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
