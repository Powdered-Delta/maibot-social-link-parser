# -*- coding: utf-8 -*-
"""pytest 根路径配置：以包上下文加载使用相对导入的插件模块。"""

from __future__ import annotations

import sys
import types
from importlib import import_module
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PACKAGE_NAME = "maibot_social_link_parser"
if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(REPO_ROOT)]
    sys.modules[PACKAGE_NAME] = package

# The adapter module imports maibot_sdk, which is only present inside the MaiBot
# Runner. Importing it eagerly broke collection of SDK-free tests (e.g. the
# ffmpeg bootstrap tests) on a plain checkout, so only load it when available
# and let the SDK-dependent tests skip themselves via importorskip.
try:
    import_module("maibot_sdk")
except ModuleNotFoundError:
    pass
else:
    sys.modules.setdefault("plugin", import_module(f"{PACKAGE_NAME}.plugin"))
