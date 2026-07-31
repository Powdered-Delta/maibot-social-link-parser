# -*- coding: utf-8 -*-
"""OneBot HTTP sender for the MaiBot multi-platform parser plugin."""
from __future__ import annotations

import asyncio
import base64
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("plugin.maibot_social_link_parser.sender")


class ApiSettings:
    host: str
    port: int
    token: str
    bot_uin: str


MessageSegment = dict[str, Any]


async def send_text(ctx: Any, message: dict[str, Any], text: str, delivery_mode: str, api: ApiSettings) -> bool:
    if delivery_mode == "websocket":
        return await _send_via_gateway(ctx, message, "text", text)
    return await _send_message(message, [{"type": "text", "data": {"text": text}}], api, timeout=30)


async def send_image(ctx: Any, message: dict[str, Any], path: Path, delivery_mode: str, api: ApiSettings) -> bool:
    if delivery_mode == "websocket":
        return await _send_via_gateway(ctx, message, "imageurl", {"url": _file_uri(path)})
    return await _send_message(message, [{"type": "image", "data": {"file": _file_uri(path)}}], api, timeout=120)


async def send_video(ctx: Any, message: dict[str, Any], path: Path, delivery_mode: str, api: ApiSettings) -> bool:
    if delivery_mode == "websocket":
        return await _send_via_gateway(ctx, message, "videourl", {"url": _file_uri(path)})
    return await _send_message(message, [{"type": "video", "data": {"file": _file_uri(path)}}], api, timeout=300)


async def send_overview_hybrid(
    ctx: Any,
    message: dict[str, Any],
    header: str,
    preview: Path,
    delivery_mode: str,
    api: ApiSettings,
) -> bool:
    """Send an overview and preview as one ordinary mixed-media message."""
    prompt = "回复“下载”获取原媒体。"
    preview_url = _file_uri(preview)
    if delivery_mode != "websocket":
        return await _send_message(
            message,
            [
                {"type": "text", "data": {"text": f"{header}\n\n"}},
                {"type": "image", "data": {"file": preview_url}},
                {"type": "text", "data": {"text": f"\n{prompt}"}},
            ],
            api,
            timeout=120,
        )

    stream_id = str(message.get("session_id") or "").strip()
    if not stream_id:
        logger.error("Cannot send hybrid overview: missing session_id")
        return False
    try:
        # Unknown Host segment types become DictComponent. The nested shape is
        # the custom component payload recognized by the NapCat adapter.
        result = await ctx.send.hybrid(
            [
                {"type": "text", "content": f"{header}\n\n"},
                {"type": "imageurl", "data": {"type": "imageurl", "data": {"url": preview_url}}},
                {"type": "text", "content": f"\n{prompt}"},
            ],
            stream_id,
            sync_to_maisaka_history=True,
        )
    except Exception as exc:
        logger.error("NapCat WebSocket hybrid overview send failed: %s", exc)
        return False
    return bool(result)


async def send_emoji_reaction(message_id: str | int, emoji_id: int, api: ApiSettings) -> bool:
    """对群消息添加 NapCat 表情回应。"""
    try:
        normalized_message_id = int(message_id)
        normalized_emoji_id = int(emoji_id)
    except (TypeError, ValueError):
        logger.warning("Cannot add emoji reaction: invalid message_id=%r emoji_id=%r", message_id, emoji_id)
        return False
    if normalized_message_id <= 0 or normalized_emoji_id <= 0:
        return False
    return await _post_onebot(
        f"http://{api.host}:{api.port}/set_msg_emoji_like",
        {"message_id": normalized_message_id, "emoji_id": normalized_emoji_id, "set": True},
        api,
        timeout=10,
    )


async def send_group_forward(
    ctx: Any,
    message: dict[str, Any],
    nodes: list[list[MessageSegment]],
    delivery_mode: str,
    api: ApiSettings,
) -> bool:
    if delivery_mode == "websocket":
        return await _send_forward_via_gateway(ctx, message, nodes)
    group_id = _get_group_id(message)
    if not group_id:
        return False
    if not nodes:
        return True

    forward_nodes = [
        {
            "type": "node",
            "data": {
                "name": "麦麦解析",
                "uin": api.bot_uin or "10000",
                "content": node,
            },
        }
        for node in nodes
    ]
    return await _post_onebot(
        f"http://{api.host}:{api.port}/send_group_forward_msg",
        {"group_id": group_id, "messages": forward_nodes},
        api,
        timeout=120,
    )


async def _send_forward_via_gateway(ctx: Any, message: dict[str, Any], nodes: list[list[MessageSegment]]) -> bool:
    """Send a merged forward through the Host's active message gateway."""
    stream_id = str(message.get("session_id") or "").strip()
    if not stream_id:
        logger.error("Cannot send merged forward through gateway: missing session_id")
        return False

    self_id = _get_self_id(message)
    messages = []
    try:
        for node in nodes:
            segments = []
            for segment in node:
                if segment.get("type") == "text":
                    segments.append({"type": "text", "content": str(segment.get("data", {}).get("text", ""))})
                elif segment.get("type") == "image":
                    image_path = _path_from_image_segment(segment)
                    segments.append(
                        {
                            "type": "image",
                            "content": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                        }
                    )
            if segments:
                messages.append({"user_id": self_id, "nickname": "麦麦解析", "segments": segments})
    except OSError as exc:
        logger.error("Cannot read merged-forward image: %s", exc)
        return False

    if not messages:
        return True
    try:
        result = await ctx.send.forward(messages, stream_id, sync_to_maisaka_history=True)
    except Exception as exc:
        logger.error("NapCat WebSocket merged-forward send failed: %s", exc)
        return False
    return bool(result)


async def _send_via_gateway(ctx: Any, message: dict[str, Any], custom_type: str, data: Any) -> bool:
    """Send through the active NapCat WebSocket gateway selected by the Host."""
    stream_id = str(message.get("session_id") or "").strip()
    if not stream_id:
        logger.error("Cannot send through gateway: missing session_id")
        return False
    try:
        if custom_type == "text":
            result = await ctx.send.text(data, stream_id, sync_to_maisaka_history=True)
        else:
            result = await ctx.send.custom(
                custom_type,
                data,
                stream_id,
                sync_to_maisaka_history=True,
                timeout_ms=300_000 if custom_type == "videourl" else 120_000,
            )
    except Exception as exc:
        logger.error("NapCat WebSocket gateway send failed: %s", exc)
        return False
    return bool(result)


def text_segment(text: str) -> MessageSegment:
    return {"type": "text", "data": {"text": text}}


def image_segment(path: Path) -> MessageSegment:
    return {"type": "image", "data": {"file": _file_uri(path)}}


async def _send_message(
    message: dict[str, Any],
    segments: list[MessageSegment],
    api: ApiSettings,
    *,
    timeout: int,
) -> bool:
    if _is_private_message(message):
        user_id = _get_user_id(message)
        if not user_id:
            logger.error("Cannot send private message: missing user id")
            return False
        url = f"http://{api.host}:{api.port}/send_private_msg"
        payload = {"user_id": user_id, "message": segments}
    else:
        group_id = _get_group_id(message)
        if not group_id:
            logger.error("Cannot send group message: missing group id")
            return False
        url = f"http://{api.host}:{api.port}/send_group_msg"
        payload = {"group_id": group_id, "message": segments}

    return await _post_onebot(url, payload, api, timeout=timeout)


async def _post_onebot(url: str, payload: dict[str, Any], api: ApiSettings, *, timeout: int) -> bool:
    token = str(getattr(api, "token", "") or "").strip()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status == 200:
                    return True

                if response.status in (401, 403) and token:
                    retry_url = f"{url}?access_token={urllib.parse.quote(token)}"
                    async with session.post(retry_url, json=payload, headers=headers, timeout=timeout) as retry:
                        if retry.status == 200:
                            return True
                        logger.error("OneBot retry failed: HTTP %s %s", retry.status, await retry.text())
                        return False

                logger.error("OneBot request failed: HTTP %s %s", response.status, await response.text())
                return False
    except asyncio.TimeoutError:
        logger.error("OneBot request timed out: %s", url)
        return False
    except Exception as exc:
        logger.error("OneBot request error: %s", exc)
        return False


def _file_uri(path: Path) -> str:
    value = str(path)
    if value.startswith(("http://", "https://", "file://")):
        return value
    return "file:///" + urllib.request.pathname2url(value).lstrip("/")


def _path_from_image_segment(segment: MessageSegment) -> Path:
    file_value = str(segment.get("data", {}).get("file", ""))
    if file_value.startswith("file://"):
        parsed = urllib.parse.urlparse(file_value)
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(file_value)


def _is_private_message(message: dict[str, Any]) -> bool:
    message_info = message.get("message_info", {})
    return bool(message_info) and message_info.get("group_info") is None


def _get_user_id(message: dict[str, Any]) -> str | None:
    message_info = message.get("message_info", {})
    user_info = message_info.get("user_info", {}) if isinstance(message_info, dict) else {}
    user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
    return str(user_id) if user_id else None


def _get_group_id(message: dict[str, Any]) -> str | None:
    message_info = message.get("message_info", {})
    group_info = message_info.get("group_info") if isinstance(message_info, dict) else None
    group_id = group_info.get("group_id") if isinstance(group_info, dict) else None
    return str(group_id) if group_id else None


def _get_self_id(message: dict[str, Any]) -> str:
    message_info = message.get("message_info", {})
    additional_config = message_info.get("additional_config", {}) if isinstance(message_info, dict) else {}
    self_id = additional_config.get("self_id") if isinstance(additional_config, dict) else None
    return str(self_id or "")
