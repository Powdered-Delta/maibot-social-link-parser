import re
from typing import ClassVar

import msgspec
from aiohttp import ClientError
from msgspec import Struct

from astrbot.api import logger

from ..config import PluginConfig
from ..cookie import CookieJar
from ..download import Downloader
from .base import BaseParser, Platform, handle


class YouTubeParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="youtube", display_name="YouTube")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.youtube
        if not self.mycfg.cookies:
            logger.debug("YouTube Cookie 未配置，部分受限视频可能解析失败")
        self.headers.update({"Referer": "https://www.youtube.com/"})
        self.cookiejar = CookieJar(config, self.mycfg, domain="youtube.com")

    # 短链 https://youtu.be/dQw4w9WgXcQ
    @handle("youtu.be", r"https?://(?:www\.)?youtu\.be/[A-Za-z0-9_\-]+")
    # 普通视频 https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s
    @handle(
        "youtube.com/watch",
        r"https?://(?:www\.|m\.)?youtube\.com/watch\?(?:[^\s]*&)?v=[A-Za-z0-9_\-]+",
    )
    # Shorts https://www.youtube.com/shorts/dQw4w9WgXcQ
    @handle(
        "youtube.com/shorts",
        r"https?://(?:www\.|m\.)?youtube\.com/shorts/[A-Za-z0-9_\-]+",
    )
    async def _parse_video(self, searched: re.Match[str]):
        return await self.parse_video(searched.group(0))

    async def parse_video(self, url: str):
        video_info = await self.downloader.ytdlp_extract_info(
            url,
            cookiefile=self.cookiejar.cookie_file,
            headers=self.headers,
            proxy=self.proxy,
        )
        author = await self._fetch_author_info(video_info)

        contents = []
        if 0 < video_info.duration <= self.cfg.max_duration:
            # yt-dlp 下载并（必要时）经 ffmpeg 合并 DASH 音视频。
            video = self.downloader.ytdlp_download_video(
                url,
                cookiefile=self.cookiejar.cookie_file,
                headers=self.headers,
                proxy=self.proxy,
                format="bv*[height<=720]+ba/b[height<=720]/b",
                node=True,
            )
            contents.append(
                self.create_video_content(
                    video,
                    video_info.thumbnail,
                    video_info.duration,
                )
            )
        elif video_info.thumbnail:
            # 超过时长限制时只回传封面，避免无谓下载。
            contents.extend(self.create_image_contents([video_info.thumbnail]))

        return self.result(
            url=url,
            title=video_info.title,
            text=video_info.description or None,
            author=author,
            contents=contents,
            timestamp=video_info.timestamp,
        )

    async def _fetch_author_info(self, video_info) -> object:
        """优先取频道头像与简介，失败时回退到视频元数据里的频道名。"""
        fallback = video_info.author_name
        channel_id = getattr(video_info, "channel_id", "") or ""
        if not channel_id:
            return self.create_author(fallback)
        try:
            browse = await self._fetch_browse(channel_id)
        except Exception as exc:
            logger.debug("YouTube 频道信息获取失败，回退到视频元数据: %s", exc)
            return self.create_author(fallback)
        return self.create_author(
            browse.name or fallback, browse.avatar_url, browse.description
        )

    async def _fetch_browse(self, channel_id: str) -> "BrowseResponse":
        url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
        payload = {
            "context": {
                "client": {
                    "hl": "zh-HK",
                    "gl": "US",
                    "deviceMake": "Apple",
                    "deviceModel": "",
                    "clientName": "WEB",
                    "clientVersion": "2.20251002.00.00",
                    "osName": "Macintosh",
                    "osVersion": "10_15_7",
                },
                "user": {"lockedSafetyMode": False},
                "request": {
                    "useSsl": True,
                    "internalExperimentFlags": [],
                    "consistencyTokenJars": [],
                },
            },
            "browseId": channel_id,
        }
        async with self.session.post(
            url,
            json=payload,
            headers=self.headers,
            proxy=self.proxy,
        ) as resp:
            if resp.status >= 400:
                raise ClientError(f"YouTube browse API {resp.status} {resp.reason}")
            return msgspec.json.decode(await resp.read(), type=BrowseResponse)


class Thumbnail(Struct):
    url: str


class AvatarInfo(Struct):
    thumbnails: list[Thumbnail] = []


class ChannelMetadataRenderer(Struct):
    title: str = ""
    description: str = ""
    avatar: AvatarInfo = AvatarInfo()


class Metadata(Struct):
    channelMetadataRenderer: ChannelMetadataRenderer


class BrowseResponse(Struct):
    metadata: Metadata

    @property
    def name(self) -> str:
        return self.metadata.channelMetadataRenderer.title

    @property
    def avatar_url(self) -> str | None:
        thumbnails = self.metadata.channelMetadataRenderer.avatar.thumbnails
        return thumbnails[0].url if thumbnails else None

    @property
    def description(self) -> str | None:
        return self.metadata.channelMetadataRenderer.description or None
