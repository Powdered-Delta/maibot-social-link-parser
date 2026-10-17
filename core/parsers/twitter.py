# -*- coding: utf-8 -*-
"""X（Twitter）解析器。

仅处理视频与图文两种形态：

- 元数据来自 X 官方 syndication 接口（``cdn.syndication.twimg.com``），无需登录，
  也不需要第三方站点；返回在 ``tweet.result`` 下的结构化 ``legacy`` 数据。
- 视频优先用 yt-dlp 下载（可正确处理 HLS/m3u8）；不可用时回退到 syndication
  给出的最高码率 mp4 直链。
- 图片取 syndication 的 ``media_url_https``，转为原图。
- 图文额外生成一张推文卡片（Pillow 可选），作为确认下载前的预览。
"""
from __future__ import annotations

import re
from typing import Any, ClassVar

import msgspec
from msgspec import Struct

from astrbot.api import logger

from .. import card
from ..config import PluginConfig
from ..cookie import CookieJar
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, Platform, handle

_SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
_STATUS_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?P<user>[0-9A-Za-z_]{1,20})/status/(?P<id>\d+)"
)
_CARD_FILENAME = "tweet-card"


class TweetPhoto(Struct):
    media_url_https: str


class TweetVideoVariant(Struct):
    bitrate: int = 0
    content_type: str = ""
    url: str = ""


class TweetVideo(Struct):
    variants: list[TweetVideoVariant] = msgspec.field(default_factory=list)

    @property
    def best_mp4(self) -> str | None:
        mp4s = [
            variant
            for variant in self.variants
            if variant.url and variant.content_type == "video/mp4"
        ]
        if not mp4s:
            return None
        return max(mp4s, key=lambda variant: variant.bitrate).url


class TweetMedia(Struct):
    photos: list[TweetPhoto] = msgspec.field(default_factory=list)
    videos: list[TweetVideo] = msgspec.field(default_factory=list)


class TweetUser(Struct):
    name: str = ""
    screen_name: str = ""


class TweetLegacy(Struct):
    full_text: str = ""
    media: TweetMedia | None = None


class TweetDetail(Struct):
    legacy: TweetLegacy = TweetLegacy()
    user: TweetUser = TweetUser()

    @property
    def text(self) -> str:
        return self.legacy.full_text or ""

    @property
    def photos(self) -> list[str]:
        media = self.legacy.media
        if media is None:
            return []
        return [photo.media_url_https for photo in media.photos if photo.media_url_https]

    @property
    def videos(self) -> list[str]:
        media = self.legacy.media
        if media is None:
            return []
        urls: list[str] = []
        for video in media.videos:
            if best := video.best_mp4:
                urls.append(best)
        return urls


class TweetResponse(Struct):
    tweet: TweetDetail | None = None

    @property
    def detail(self) -> TweetDetail:
        if self.tweet is None:
            raise ParseException("推文不存在、已删除或受保护")
        return self.tweet


class TwitterParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="twitter", display_name="X")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.twitter
        self.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://x.com/",
            }
        )
        self.cookiejar = CookieJar(config, self.mycfg, domain="x.com")
        if self.cookiejar.cookies_str:
            self.headers["cookie"] = self.cookiejar.cookies_str

    @handle("x.com", r"https?://(?:www\.|mobile\.)?x\.com/[0-9A-Za-z_]{1,20}/status/\d+")
    @handle(
        "twitter.com",
        r"https?://(?:www\.|mobile\.)?twitter\.com/[0-9A-Za-z_]{1,20}/status/\d+",
    )
    async def _parse(self, searched: re.Match[str]):
        url = searched.group(0)
        status_id = _STATUS_RE.search(url).group("id")
        detail = await self._fetch_syndication(status_id)
        return await self._build_result(url, detail)

    async def _fetch_syndication(self, status_id: str) -> TweetDetail:
        params = {"id": status_id, "lang": "zh-cn", "token": "a"}
        async with self.session.get(
            _SYNDICATION, params=params, headers=self.headers, proxy=self.proxy
        ) as resp:
            if resp.status == 404:
                raise ParseException("推文不存在、已删除或受保护")
            if resp.status >= 400:
                raise ParseException(f"X 解析失败（HTTP {resp.status}）")
            payload = await resp.read()
        try:
            return msgspec.json.decode(payload, type=TweetResponse).detail
        except msgspec.ValidationError as exc:
            raise ParseException(f"X 返回数据结构异常：{exc}") from exc

    async def _build_result(self, url: str, detail: TweetDetail):
        author = self.create_author(
            detail.user.name or detail.user.screen_name or "X",
            profile_url=f"https://x.com/{detail.user.screen_name}"
            if detail.user.screen_name
            else None,
        )
        contents: list[Any] = []

        if detail.videos:
            video_url = detail.videos[0]
            cover = self._pick_cover(detail)
            video_task = await self._download_video(url, video_url)
            if video_task is not None:
                content = self.create_video_content(video_task)
                if cover:
                    content.cover = self.downloader.download_img.__wrapped__(
                        self.downloader, cover, headers=self.headers, proxy=self.proxy
                    )
                contents.append(content)
        elif detail.photos:
            contents.extend(self.create_image_contents(detail.photos, headers=self.headers))

        card_path = await self._render_card(detail, media_count=len(contents))
        if card_path is not None:
            from ..data import ImageContent

            contents.insert(0, ImageContent(card_path))

        if not contents:
            raise ParseException("该推文没有可下载的图片或视频")

        return self.result(
            url=url,
            title=(detail.text.splitlines() or [""])[0][:120] or None,
            text=detail.text or None,
            author=author,
            contents=contents,
        )

    async def _download_video(self, tweet_url: str, mp4_url: str):
        """优先 yt-dlp（可处理 m3u8），失败回退 syndication 的 mp4 直链。"""
        try:
            return self.downloader.ytdlp_download_video(
                tweet_url,
                cookiefile=self.cookiejar.cookie_file,
                headers=self.headers,
                proxy=self.proxy,
                format="bv*+ba/b",
            )
        except Exception as exc:
            logger.debug("yt-dlp 下载推文视频失败，回退直链: %s", exc)
        return self.downloader.download_video.__wrapped__(
            self.downloader, mp4_url, headers=self.headers, proxy=self.proxy
        )

    @staticmethod
    def _pick_cover(detail: TweetDetail) -> str | None:
        return detail.photos[0] if detail.photos else None

    async def _render_card(self, detail: TweetDetail, *, media_count: int):
        if not card.available():
            return None
        out_path = self.cfg.cache_dir / f"{_CARD_FILENAME}.png"
        try:
            return await card.render_tweet_card_async(
                out_path,
                author=detail.user.name,
                handle=detail.user.screen_name,
                text=detail.text,
                media_count=media_count,
            )
        except Exception as exc:
            logger.debug("推文卡片生成失败，忽略: %s", exc)
            return None
