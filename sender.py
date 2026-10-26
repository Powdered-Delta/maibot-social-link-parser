# -*- coding: utf-8 -*-
"""OneBot HTTP sender for the MaiBot multi-platform parser plugin."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("plugin.maibot_social_link_parser.sender")

# Transient send failures (connection reset, timeout, 5xx) are retried with a
# small backoff so a single hiccup does not silently drop media. Bounded and
# only for transport/5xx errors: a definitive 4xx is surfaced immediately.
SEND_RETRY_TIMES = 2
SEND_RETRY_BACKOFF = 0.8

# Both containers mount the plugin data directory at the same absolute path
# (``./data/MaiMBot-plugin-data`` -> ``/MaiMBot/data/plugins``), so a path that is
# valid for MaiBot is also valid for NapCat. Forwarding images as paths keeps the
# WebSocket frame small instead of base64-inlining megabytes of gallery data.
SHARED_DATA_ROOT = "/MaiMBot/data/plugins"

# When the shared volume is not in play (or the file is outside it), inline the
# image bytes, but only below this size: a single oversized frame fails the whole
# forward, losing every node in it.
INLINE_IMAGE_MAX_BYTES = 256 * 1024


class ApiSettings:
    host: str
    port: int
    token: str
    bot_uin: str


MessageSegment = dict[str, Any]


async def _with_retry(send: Any, *, label: str) -> bool:
    """Run an async send callable, retrying transient failures with backoff.

    Retries are bounded and only applied to exceptions; callers are responsible
    for not raising on definitive (non-retryable) failures.
    """
    for attempt in range(SEND_RETRY_TIMES + 1):
        try:
            return bool(await send())
        except Exception as exc:
            if attempt >= SEND_RETRY_TIMES:
                logger.error("%s failed after retries: %s", label, exc)
                return False
            delay = SEND_RETRY_BACKOFF * (2**attempt) + random.uniform(0, 0.2)
            logger.warning(
                "%s failed, retrying in %ss (%s/%s): %s",
                label,
                round(delay, 1),
                attempt + 1,
                SEND_RETRY_TIMES,
                exc,
            )
            await asyncio.sleep(delay)
    return False


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
    segments = [
        {"type": "text", "content": f"{header}\n\n"},
        {"type": "imageurl", "data": {"type": "imageurl", "data": {"url": preview_url}}},
        {"type": "text", "content": f"\n{prompt}"},
    ]

    async def send():
        # Unknown Host segment types become DictComponent. The nested shape is
        # the custom component payload recognized by the NapCat adapter.
        return await ctx.send.hybrid(segments, stream_id, sync_to_maisaka_history=True)

    return await _with_retry(send, label="NapCat WebSocket hybrid overview send")


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
                    # The adapter's text builder reads ``data`` verbatim, not
                    # ``content``; anything else renders as an empty segment.
                    segments.append(
                        {"type": "text", "data": str(segment.get("data", {}).get("text", ""))}
                    )
                elif segment.get("type") == "image":
                    image_path = _path_from_image_segment(segment)
                    image_segment_payload = _forward_image_segment(image_path)
                    if image_segment_payload is not None:
                        segments.append(image_segment_payload)
            if segments:
                messages.append({"user_id": self_id, "nickname": "麦麦解析", "segments": segments})
    except OSError as exc:
        logger.error("Cannot read merged-forward image: %s", exc)
        return False

    if not messages:
        return True

    async def send():
        return await ctx.send.forward(messages, stream_id, sync_to_maisaka_history=True)

    return await _with_retry(send, label="NapCat WebSocket merged-forward send")


async def _send_via_gateway(ctx: Any, message: dict[str, Any], custom_type: str, data: Any) -> bool:
    """Send through the active NapCat WebSocket gateway selected by the Host."""
    stream_id = str(message.get("session_id") or "").strip()
    if not stream_id:
        logger.error("Cannot send through gateway: missing session_id")
        return False

    async def send():
        if custom_type == "text":
            return await ctx.send.text(data, stream_id, sync_to_maisaka_history=True)
        return await ctx.send.custom(
            custom_type,
            data,
            stream_id,
            sync_to_maisaka_history=True,
            timeout_ms=300_000 if custom_type == "videourl" else 120_000,
        )

    return await _with_retry(send, label=f"NapCat WebSocket {custom_type} send")


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
    return await _with_retry(
        lambda: _post_onebot_once(url, payload, api, timeout=timeout),
        label="OneBot request",
    )


async def _post_onebot_once(url: str, payload: dict[str, Any], api: ApiSettings, *, timeout: int) -> bool:
    token = str(getattr(api, "token", "") or "").strip()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

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

            if response.status >= 500:
                # Server-side hiccup is transient; raise so _with_retry retries.
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message=response.reason,
                )

            logger.error("OneBot request failed: HTTP %s %s", response.status, await response.text())
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


def _shared_path_for(path: Path) -> str | None:
    """Return the shared-volume path NapCat can read, or None if not shared."""
    # Keep this POSIX-string based: the plugin always runs inside a Linux
    # container, and os.path.abspath would rewrite "/MaiMBot/..." into a
    # drive-letter path on a Windows test host, breaking the prefix check.
    resolved = os.path.normpath(str(path)).replace("\\", "/")
    root = os.path.normpath(SHARED_DATA_ROOT).replace("\\", "/")
    if resolved == root or resolved.startswith(root + "/"):
        return resolved
    return None


def _forward_image_segment(path: Path) -> MessageSegment | None:
    """Build one forward node image segment without bloating the WebSocket frame.

    Prefer the shared-volume absolute path: NapCat mounts the same directory at the
    same location, so it resolves the file itself and the frame stays tiny. Only
    fall back to inlining bytes for files outside the shared root, and only when
    they are small enough that a single frame cannot fail the whole forward.

    The payload must be nested as ``data = {"type": "imageurl", "data": {"url": ...}}``,
    not flat as ``data = {"url": ...}``. Any unknown plugin segment type becomes a
    ``DictComponent`` in the Host (``message_utils._component_from_dict``), and the
    DictComponent payload is what the adapter sees. A flat payload therefore arrives
    with no ``type`` field, so ``_build_dict_component_segment`` cannot recognise
    ``imageurl`` and degrades it to ``[unsupported:dict]`` in QQ. The nested shape
    keeps the inner ``type`` the adapter dispatches on.

    Do not switch this back to ``image`` + ``content`` either: the adapter's
    ``_build_image_segments`` only reads ``binary_data_base64`` and returns an empty
    list otherwise, which renders as a bare ``[image]`` placeholder.
    """
    shared_path = _shared_path_for(path)
    if shared_path is not None and path.is_file():
        return _nested_imageurl_segment(f"file://{shared_path}")

    if not path.is_file():
        logger.warning("Skipping missing forward image: %s", path)
        return None
    size = path.stat().st_size
    if size > INLINE_IMAGE_MAX_BYTES:
        logger.warning(
            "Skipping oversized inline forward image (%s bytes, limit %s): %s",
            size,
            INLINE_IMAGE_MAX_BYTES,
            path,
        )
        return None
    import base64 as _base64

    return _nested_imageurl_segment(f"base64://{_base64.b64encode(path.read_bytes()).decode('ascii')}")


def _nested_imageurl_segment(url: str) -> MessageSegment:
    """Build the nested ``imageurl`` segment the adapter dispatches on.

    Args:
        url: ``file://`` shared-volume path or ``base64://`` payload.

    Returns:
        MessageSegment: Nested segment; see ``_forward_image_segment`` for why it
        cannot be flat.
    """
    return {
        "type": "imageurl",
        "data": {"type": "imageurl", "data": {"url": url}},
    }



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
