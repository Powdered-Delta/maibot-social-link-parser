# -*- coding: utf-8 -*-
"""可选的推文卡片渲染。

把推文作者与正文合成为一张图片，作为确认下载前的概览预览。Pillow 或可用
字体缺失时返回 None，解析与发送流程均不受影响。
"""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

from astrbot.api import logger

try:  # Pillow 为可选依赖：缺失时卡片功能自动关闭
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - 取决于运行环境
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)

_WIDTH = 760
_MARGIN = 28
_WRAP = 30
_MAX_LINES = 22
_BG = (255, 255, 255)
_FG = (15, 20, 25)
_SUB = (83, 100, 113)
_AVATAR_BG = (233, 238, 242)
_RULE = (207, 217, 222)


def available() -> bool:
    """Pillow 是否可用。"""
    return Image is not None and ImageDraw is not None and ImageFont is not None


def _load_font(size: int):
    for path in _FONT_CANDIDATES:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - 逐个候选尝试
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def render_tweet_card(
    out_path: Path,
    *,
    author: str = "",
    handle: str = "",
    text: str = "",
    media_count: int = 0,
) -> Path | None:
    """渲染推文卡片；Pillow/字体不可用时返回 None。"""
    if not available():
        return None
    font_title = _load_font(30)
    font_body = _load_font(26)
    font_meta = _load_font(22)
    if font_title is None or font_body is None or font_meta is None:
        return None

    body = text.strip() or "(无正文)"
    wrapped: list[str] = []
    for raw_line in body.splitlines():
        wrapped.extend(textwrap.wrap(raw_line, width=_WRAP) or [""])
    if len(wrapped) > _MAX_LINES:
        wrapped = wrapped[:_MAX_LINES]
        wrapped[-1] = wrapped[-1][: _WRAP - 1] + "…"
    if not wrapped:
        wrapped = ["(无正文)"]

    line_h = 40
    header_h = 96
    footer_h = 52 if media_count else 12
    height = _MARGIN * 2 + header_h + line_h * len(wrapped) + footer_h

    image = Image.new("RGB", (_WIDTH, height), _BG)
    draw = ImageDraw.Draw(image)

    x, y = _MARGIN, _MARGIN
    draw.ellipse((x, y, x + 56, y + 56), fill=_AVATAR_BG)
    initial = (author or handle or "?")[:1].upper()
    initial_w = draw.textlength(initial, font=font_title)
    draw.text((x + (56 - initial_w) / 2, y + 8), initial, font=font_title, fill=_SUB)
    draw.text((x + 72, y + 2), author or "X", font=font_title, fill=_FG)
    if handle:
        draw.text((x + 72, y + 38), f"@{handle}", font=font_meta, fill=_SUB)

    y += header_h
    for line in wrapped:
        draw.text((x, y), line, font=font_body, fill=_FG)
        y += line_h

    if media_count:
        y += 4
        draw.line((x, y, _WIDTH - _MARGIN, y), fill=_RULE, width=1)
        draw.text((x, y + 10), f"含 {media_count} 项媒体", font=font_meta, fill=_SUB)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG")
    logger.debug("推文卡片已生成: %s", out_path)
    return out_path


async def render_tweet_card_async(
    out_path: Path,
    *,
    author: str = "",
    handle: str = "",
    text: str = "",
    media_count: int = 0,
) -> Path | None:
    """异步封装的推文卡片渲染，避免阻塞事件循环。"""
    return await asyncio.to_thread(
        render_tweet_card,
        out_path,
        author=author,
        handle=handle,
        text=text,
        media_count=media_count,
    )
