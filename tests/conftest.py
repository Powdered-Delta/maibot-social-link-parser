# -*- coding: utf-8 -*-
"""pytest 根路径配置：将插件仓库根目录加入 sys.path，便于 `from plugin import ...`。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
