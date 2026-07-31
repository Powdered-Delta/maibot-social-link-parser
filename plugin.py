# -*- coding: utf-8 -*-
"""MaiBot adapter for Zhalslar/astrbot_plugin_parser core parsers."""
from __future__ import annotations

import asyncio
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode, HookOrder

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from .core.config import PluginConfig as CorePluginConfig
from .core.data import DynamicContent, GraphicsContent, ImageContent, ParseResult, TextContent, VideoContent
from .core.download import Downloader
from .core.exception import ParseException
from .core.parsers import BaseParser
from .sender import (
    ApiSettings,
    image_segment,
    send_emoji_reaction,
    send_group_forward,
    send_image,
    send_text,
    send_video,
    text_segment,
)

URL_RE = re.compile(r"https?://[^\s\]\)）>\"']+")


class PluginSectionConfig(PluginConfigBase):
    name: str = Field(default="maibot_social_link_parser", description="插件名称")
    config_version: str = Field(default="0.1.1", description="配置文件版本")
    version: str = Field(default="0.1.1", description="插件版本")
    enabled: bool = Field(default=True, description="是否启用插件")
    dry_run: bool = Field(default=False, description="演练模式：不向 QQ/NapCat 发送消息，仅记录拟发送内容；仍执行解析和下载")


class ParserSectionConfig(PluginConfigBase):
    enabled_platforms: list[Literal["douyin", "xhs", "xiaoheihe", "bilibili"]] = Field(
        default=["douyin", "xhs", "xiaoheihe"],
        description="多选启用的平台。B站默认关闭以避免与独立 B站插件重复回复。",
        json_schema_extra={
            "widget": "checkboxes",
            "options": [
                {"label": "抖音", "value": "douyin"},
                {"label": "小红书", "value": "xhs"},
                {"label": "小黑盒", "value": "xiaoheihe"},
                {"label": "B站（默认关闭）", "value": "bilibili"},
            ],
        },
    )
    group_whitelist: list[str] = Field(default_factory=list, description="只在这些 QQ 群自动解析；空列表表示所有群")
    block_ai_reply: bool = Field(default=True, description="命中链接后阻止麦麦继续触发普通聊天")
    debounce_seconds: int = Field(default=120, description="同一会话同一链接去重时间，单位秒", ge=0)
    max_images: int = Field(default=9, description="单条链接最多发送图片数", ge=1, le=30)
    max_text_chars: int = Field(default=700, description="摘要正文最大字符数", ge=80, le=3000)
    send_images: bool = Field(default=True, description="是否发送图片")
    send_video: bool = Field(default=True, description="是否发送视频")
    use_forward_for_multi: bool = Field(default=True, description="群聊多图/图文是否使用合并转发")
    download_mode: str = Field(default="confirm", description="下载模式：confirm=确认后下载，automatic=自动下载")
    pending_download_ttl_seconds: int = Field(default=600, description="等待用户确认下载的有效期秒数", ge=60, le=3600)
    send_preview_image: bool = Field(default=True, description="确认模式下是否发送视频封面或图集首图")
    reaction_emoji_enabled: bool = Field(default=True, description="识别到链接后是否对原消息贴表情")
    reaction_emoji_id: int = Field(default=76, description="NapCat QQ 表情回应 ID", ge=1)
    source_max_size_mb: int = Field(default=80, description="单个媒体最大下载大小 MB", ge=1, le=300)
    source_max_minutes: int = Field(default=8, description="视频最大时长分钟数", ge=1, le=60)


class NetworkSectionConfig(PluginConfigBase):
    proxy: str = Field(default="", description="解析/下载代理，例如 http://127.0.0.1:7890；留空不用代理")
    use_proxy_platforms: list[str] = Field(default=[], description="需要走代理的平台名")
    common_timeout: int = Field(default=30, description="普通请求超时秒数", ge=5, le=120)
    download_timeout: int = Field(default=120, description="下载超时秒数", ge=10, le=600)
    download_retry_times: int = Field(default=1, description="下载重试次数", ge=0, le=5)


class CookieSectionConfig(PluginConfigBase):
    douyin: str = Field(default="", description="抖音 Cookie，可选")
    xhs: str = Field(default="", description="小红书 Cookie，可选")
    xiaoheihe: str = Field(default="", description="小黑盒 Cookie，可选")
    bilibili: str = Field(default="", description="B站 Cookie，可选")


class ApiConfig(PluginConfigBase):
    host: str = Field(default="127.0.0.1", description="OneBot HTTP API 主机")
    port: int = Field(default=3000, description="OneBot HTTP API 端口", ge=1, le=65535)
    token: str = Field(default="", description="OneBot HTTP API Token")
    bot_uin: str = Field(default="", description="发送合并转发节点时使用的 bot QQ")


class PluginConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    parser: ParserSectionConfig = Field(default_factory=ParserSectionConfig)
    network: NetworkSectionConfig = Field(default_factory=NetworkSectionConfig)
    cookies: CookieSectionConfig = Field(default_factory=CookieSectionConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


@dataclass(slots=True)
class PendingDownload:
    """A confirmation-bound download request for one sender in one conversation."""

    parser: BaseParser
    keyword: str
    searched: Any
    url: str
    expires_at: float


class MultiPlatformParserPlugin(MaiBotPlugin):
    """自动解析抖音、小红书、小黑盒等社媒链接。"""

    config_model = PluginConfig
    config_reload_subscriptions: tuple[str, ...] = ()

    _downloader: Downloader | None = None
    _parsers: list[BaseParser] = []
    _recent: dict[tuple[str, str], float] = {}
    _pending_downloads: dict[tuple[str, str], PendingDownload] = {}
    _tasks: set[asyncio.Task[Any]] = set()

    async def on_load(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("Multi platform parser disabled")
            return
        core_cfg = self._build_core_config()
        self._downloader = Downloader(core_cfg)

        enabled = set(core_cfg.parser.enabled_platforms())
        self._parsers = [
            parser_cls(core_cfg, self._downloader)
            for parser_cls in BaseParser.get_all_subclass()
            if parser_cls.platform.name in enabled
        ]
        names = ", ".join(parser.platform.display_name for parser in self._parsers) or "none"
        self.ctx.logger.info("Multi platform parser loaded: %s", names)

    async def on_unload(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._pending_downloads.clear()
        for parser in self._parsers:
            await parser.close_session()
        self._parsers = []
        if self._downloader is not None:
            await self._downloader.close()
            self._downloader = None
        self.ctx.logger.info("Multi platform parser unloaded")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del scope, config_data
        self.ctx.logger.info("Multi platform parser config updated to %s", version)

    @HookHandler(
        hook="chat.receive.after_process",
        name="social_link_parser_hook",
        description="自动解析抖音/小红书/小黑盒等平台链接",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def handle_link(self, **kwargs) -> dict[str, Any] | None:
        if not self.config.plugin.enabled or not self._parsers:
            return None

        message: dict[str, Any] = kwargs.get("message", {}) or {}
        if not self._is_allowed_group(message):
            return None

        source = self._message_source(message)
        candidates = self._extract_urls(source)
        session_id = self._session_id(message)
        sender_id = self._get_user_id(message)
        if not candidates and self._is_download_confirmation(source):
            pending = self._consume_pending_download(session_id, sender_id)
            if pending is not None:
                await self._send_text(message, "好的，开始下载。")
                self._create_task(self._download_pending_task(pending, message), "link_resolver.download")
                return {"action": "abort"} if self.config.parser.block_ai_reply else None
        matched: tuple[BaseParser, str, Any, str] | None = None
        for url in candidates:
            for parser in self._parsers:
                try:
                    keyword, searched = parser.search_url(url)
                    matched = (parser, keyword, searched, url)
                    break
                except ParseException:
                    continue
            if matched:
                break

        if not matched:
            return None

        parser, keyword, searched, url = matched
        if self._is_recent(session_id, url):
            return {"action": "abort"} if self.config.parser.block_ai_reply else None

        self.ctx.logger.info("Multi platform link detected: %s %s", parser.platform.display_name, url)
        self._create_task(self._process_task(parser, keyword, searched, url, message), "link_resolver.parse")

        if self.config.parser.block_ai_reply:
            return {"action": "abort"}
        return None

    async def _process_task(self, parser: BaseParser, keyword: str, searched: Any, url: str, message: dict[str, Any]) -> None:
        try:
            result = await parser.parse(keyword, searched)
            if not result.url:
                result.url = url
            if self.config.parser.reaction_emoji_enabled and self._get_group_id(message):
                await self._send_emoji_reaction(message.get("message_id", ""), self.config.parser.reaction_emoji_id)
            if self.config.parser.download_mode == "automatic":
                await self._send_result(message, result)
                return
            await self._send_overview(message, result)
            self._store_pending_download(message, parser, keyword, searched, url)
        except Exception as exc:
            self.ctx.logger.warning("Multi platform parser failed for %s: %s", url, exc)
            await self._send_text(message, "这个链接解析失败了，请稍后重试。")

    async def _download_pending_task(self, pending: PendingDownload, message: dict[str, Any]) -> None:
        try:
            result = await pending.parser.parse(pending.keyword, pending.searched)
            if not result.url:
                result.url = pending.url
            await self._send_result(message, result)
        except Exception as exc:
            self.ctx.logger.warning("Confirmed media download failed for %s: %s", pending.url, exc)
            await self._send_text(message, "媒体下载失败，请稍后重试。")

    async def _send_overview(self, message: dict[str, Any], result: ParseResult) -> None:
        """Send metadata and one preview only; full media waits for user confirmation."""
        header = self._format_header(result)
        await self._send_text(message, f"{header}\n\n回复“下载”获取原媒体。")
        if not self.config.parser.send_preview_image:
            self._cancel_media_tasks(self._flatten_contents(result))
            return

        preview = await self._get_preview_path(self._flatten_contents(result))
        self._cancel_media_tasks(self._flatten_contents(result), keep_path=preview)
        self._cancel_author_avatar(result)
        if preview is not None:
            await self._send_image(message, preview)

    async def _send_result(self, message: dict[str, Any], result: ParseResult) -> None:
        header = self._format_header(result)
        text_nodes: list[list[dict[str, Any]]] = []
        media_items = self._flatten_contents(result)

        if header:
            text_nodes.append([text_segment(header)])

        image_nodes: list[list[dict[str, Any]]] = []
        videos: list[VideoContent | DynamicContent] = []

        image_count = 0
        for content in media_items:
            if isinstance(content, TextContent):
                if content.text:
                    text_nodes.append([text_segment(self._truncate(content.text, self.config.parser.max_text_chars))])
            elif isinstance(content, GraphicsContent):
                if content.text:
                    image_nodes.append([text_segment(self._truncate(content.text, self.config.parser.max_text_chars))])
                if self.config.parser.send_images and image_count < self.config.parser.max_images:
                    image_count += 1
                    image_slot = image_count
                    try:
                        path = await content.get_path()
                    except Exception as exc:
                        self.ctx.logger.warning("Graphic %s download failed after retries: %s", image_slot, exc)
                        image_nodes.append([text_segment(f"第 {image_slot} 张图片下载失败，已跳过。")])
                    else:
                        image_nodes.append([image_segment(path)])
            elif isinstance(content, ImageContent):
                if self.config.parser.send_images and image_count < self.config.parser.max_images:
                    image_count += 1
                    image_slot = image_count
                    try:
                        path = await content.get_path()
                    except Exception as exc:
                        self.ctx.logger.warning("Image %s download failed after retries: %s", image_slot, exc)
                        image_nodes.append([text_segment(f"第 {image_slot} 张图片下载失败，已跳过。")])
                    else:
                        image_nodes.append([image_segment(path)])
            elif isinstance(content, (VideoContent, DynamicContent)):
                videos.append(content)

        nodes = text_nodes + image_nodes
        sent_forward = False
        if self.config.parser.use_forward_for_multi and len(nodes) > 1 and self._get_group_id(message):
            sent_forward = await self._send_group_forward(message, nodes)

        if not sent_forward:
            if header:
                await self._send_text(message, header)
            for node in image_nodes:
                segment = node[0]
                if segment.get("type") == "text":
                    await self._send_text(message, segment["data"]["text"])
                elif segment.get("type") == "image":
                    await self._send_image(message, self._path_from_file_segment(segment))

        if self.config.parser.send_video:
            for video in videos[:1]:
                try:
                    path = await video.get_path()
                except Exception as exc:
                    self.ctx.logger.warning("Skipping unavailable video: %s", exc)
                    continue
                await self._send_video(message, path)

    async def _send_text(self, message: dict[str, Any], text: str) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] text: %s", text)
            return True
        return await send_text(message, text, self.config.api)

    async def _send_image(self, message: dict[str, Any], path: Path) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] image: path=%s exists=%s", path.name, path.is_file())
            return True
        return await send_image(message, path, self.config.api)

    async def _send_video(self, message: dict[str, Any], path: Path) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] video: path=%s exists=%s size=%s", path.name, path.is_file(), path.stat().st_size if path.is_file() else 0)
            return True
        return await send_video(message, path, self.config.api)

    async def _send_group_forward(self, message: dict[str, Any], nodes: list[list[dict[str, Any]]]) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] group_forward: nodes=%s", len(nodes))
            return True
        return await send_group_forward(message, nodes, self.config.api)

    async def _send_emoji_reaction(self, message_id: str | int, emoji_id: int) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] emoji_reaction: message_id=%s emoji_id=%s", message_id, emoji_id)
            return True
        return await send_emoji_reaction(message_id, emoji_id, self.config.api)

    def _build_core_config(self) -> CorePluginConfig:
        data_dir = self.ctx.paths.data_dir
        return CorePluginConfig(
            data_dir=data_dir,
            enabled_platforms=list(self.config.parser.enabled_platforms),
            proxy=(self.config.network.proxy or "").strip() or None,
            source_max_size=self.config.parser.source_max_size_mb,
            source_max_minute=self.config.parser.source_max_minutes,
            debounce_interval=self.config.parser.debounce_seconds,
            download_timeout=self.config.network.download_timeout,
            download_retry_times=self.config.network.download_retry_times,
            common_timeout=self.config.network.common_timeout,
            bilibili_cookies=self.config.cookies.bilibili,
            douyin_cookies=self.config.cookies.douyin,
            xhs_cookies=self.config.cookies.xhs,
            xiaoheihe_cookies=self.config.cookies.xiaoheihe,
            use_proxy_platforms=list(self.config.network.use_proxy_platforms),
        )

    def _format_header(self, result: ParseResult) -> str:
        parts = [f"{result.platform.display_name}解析"]
        if result.author and result.author.name:
            parts.append(f"作者：{result.author.name}")
        if result.title:
            parts.append(f"标题：{self._truncate(result.title, 120)}")
        if result.text:
            parts.append(self._truncate(result.text, self.config.parser.max_text_chars))
        if result.extra_info:
            parts.append(str(result.extra_info))
        if result.url:
            parts.append(f"链接：{result.url}")
        return "\n".join(part for part in parts if part)

    def _flatten_contents(self, result: ParseResult) -> list[Any]:
        contents: list[Any] = []
        if result.send_groups:
            for group in result.send_groups:
                contents.extend(group.contents)
        else:
            contents.extend(result.contents)
        if result.repost:
            contents.extend(self._flatten_contents(result.repost))
        return contents

    def _message_source(self, message: dict[str, Any]) -> str:
        parts = [str(message.get("processed_plain_text", "") or "")]
        for seg in message.get("raw_message", []) or []:
            if not isinstance(seg, dict):
                continue
            parts.extend(self._urls_from_payload(seg))
        return "\n".join(parts)

    def _extract_urls(self, text: str) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        for match in URL_RE.finditer(text):
            url = match.group(0).rstrip(".,，。!！?？")
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _urls_from_payload(self, value: Any, depth: int = 0) -> list[str]:
        if depth > 5:
            return []
        if isinstance(value, str):
            return self._extract_urls(value)
        if isinstance(value, dict):
            urls: list[str] = []
            for item in value.values():
                urls.extend(self._urls_from_payload(item, depth + 1))
            return urls
        if isinstance(value, list):
            urls = []
            for item in value:
                urls.extend(self._urls_from_payload(item, depth + 1))
            return urls
        return []

    async def _get_preview_path(self, contents: list[Any]) -> Path | None:
        for content in contents:
            try:
                if isinstance(content, VideoContent):
                    cover = await content.get_cover_path()
                    if cover is not None:
                        return cover
                elif isinstance(content, (GraphicsContent, ImageContent)):
                    return await content.get_path()
            except Exception as exc:
                self.ctx.logger.warning("Preview download failed: %s", exc)
        return None

    @staticmethod
    def _cancel_media_tasks(contents: list[Any], keep_path: Path | None = None) -> None:
        for content in contents:
            for candidate in (getattr(content, "path_task", None), getattr(content, "cover", None)):
                if isinstance(candidate, asyncio.Task) and not candidate.done():
                    candidate.cancel()
                elif hasattr(candidate, "close"):
                    candidate.close()

    @staticmethod
    def _cancel_author_avatar(result: ParseResult) -> None:
        author = result.author
        candidate = getattr(author, "avatar", None) if author is not None else None
        if isinstance(candidate, asyncio.Task) and not candidate.done():
            candidate.cancel()
        elif hasattr(candidate, "close"):
            candidate.close()

    def _store_pending_download(self, message: dict[str, Any], parser: BaseParser, keyword: str, searched: Any, url: str) -> None:
        sender_id = self._get_user_id(message)
        if not sender_id:
            return
        self._pending_downloads[(self._session_id(message), sender_id)] = PendingDownload(
            parser=parser,
            keyword=keyword,
            searched=searched,
            url=url,
            expires_at=time.time() + self.config.parser.pending_download_ttl_seconds,
        )

    def _consume_pending_download(self, session_id: str, sender_id: str | None) -> PendingDownload | None:
        if not sender_id:
            return None
        key = (session_id, sender_id)
        pending = self._pending_downloads.pop(key, None)
        if pending is None or pending.expires_at < time.time():
            return None
        return pending

    @staticmethod
    def _is_download_confirmation(text: str) -> bool:
        return bool(re.search(r"(?:^|[，,。！!\s])(?:下载|帮我下载|发视频|保存)(?:[。！!\s]|$)", text.strip()))

    def _create_task(self, coroutine: Any, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _session_id(self, message: dict[str, Any]) -> str:
        return str(message.get("session_id", "") or self._group_or_user_id(message) or "unknown")

    def _is_allowed_group(self, message: dict[str, Any]) -> bool:
        group_id = self._get_group_id(message)
        if not group_id:
            return True
        whitelist = [str(item) for item in self.config.parser.group_whitelist if str(item).strip()]
        return not whitelist or group_id in whitelist

    def _is_recent(self, session_id: str, url: str) -> bool:
        interval = int(self.config.parser.debounce_seconds)
        if interval <= 0:
            return False
        now = time.time()
        key = (session_id, url)
        expires_at = self._recent.get(key, 0)
        self._recent[key] = now + interval
        for old_key, old_expires in list(self._recent.items()):
            if old_expires < now:
                self._recent.pop(old_key, None)
        return expires_at > now

    def _group_or_user_id(self, message: dict[str, Any]) -> str | None:
        return self._get_group_id(message) or self._get_user_id(message)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        text = str(text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "…"

    @staticmethod
    def _path_from_file_segment(segment: dict[str, Any]) -> Path:
        file_value = str(segment.get("data", {}).get("file", ""))
        if file_value.startswith("file://"):
            parsed = urllib.parse.urlparse(file_value)
            return Path(urllib.request.url2pathname(parsed.path))
        return Path(file_value)

    @staticmethod
    def _get_group_id(message: dict[str, Any]) -> str | None:
        message_info = message.get("message_info", {})
        group_info = message_info.get("group_info") if isinstance(message_info, dict) else None
        group_id = group_info.get("group_id") if isinstance(group_info, dict) else None
        return str(group_id) if group_id else None

    @staticmethod
    def _get_user_id(message: dict[str, Any]) -> str | None:
        message_info = message.get("message_info", {})
        user_info = message_info.get("user_info", {}) if isinstance(message_info, dict) else {}
        user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
        return str(user_id) if user_id else None


def create_plugin() -> MultiPlatformParserPlugin:
    return MultiPlatformParserPlugin()
