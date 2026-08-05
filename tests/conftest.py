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

sys.modules.setdefault("plugin", import_module(f"{PACKAGE_NAME}.plugin"))
