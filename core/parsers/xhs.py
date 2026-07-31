import html
import json
import re
import urllib.parse
from typing import Any, ClassVar

from msgspec import Struct, convert, field

from astrbot.api import logger

from ..config import PluginConfig
from ..cookie import CookieJar
from ..data import ImageContent
from ..download import Downloader
from .base import BaseParser, ParseException, Platform, handle


class XHSParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="xhs", display_name="小红书")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.xhs
        self.cookies = self.mycfg.cookies
        self.headers.update(
            {
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
                )
            }
        )
        self.ios_headers.update(
            {
                "origin": "https://www.xiaohongshu.com",
                "x-requested-with": "XMLHttpRequest",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
        )
        self.cookiejar = CookieJar(config, self.mycfg, domain="xiaohongshu.com")
        if self.cookiejar.cookies_str:
            self.headers["cookie"] = self.cookiejar.cookies_str
            self.ios_headers["cookie"] = self.cookiejar.cookies_str

    @handle("xhslink.com", r"xhslink\.(?:com|cn)/[A-Za-z0-9._?%&+=/#@-]+")
    @handle("xhslink.cn", r"xhslink\.(?:com|cn)/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url, self.ios_headers)

    # https://www.xiaohongshu.com/discovery/item/68e8e3fa00000000030342ec?app_platform=android&ignoreEngage=true&app_version=9.6.0&share_from_user_hidden=true&xsec_source=app_share&type=normal&xsec_token=CBW9rwIV2qhcCD-JsQAOSHd2tTW9jXAtzqlgVXp6c52Sw%3D&author_share=1&xhsshare=QQ&shareRedId=ODs3RUk5ND42NzUyOTgwNjY3OTo8S0tK&apptime=1761372823&share_id=3b61945239ac403db86bea84a4f15124&share_channel=qq
    @handle(
        "xiaohongshu.com",
        r"(explore|discovery/item)/(?P<query>(?P<xhs_id>[0-9a-zA-Z]+)\?[A-Za-z0-9._%&+=/#@-]+)",
    )
    async def _parse_common(self, searched: re.Match[str]):
        xhs_domain = "https://www.xiaohongshu.com"
        query, xhs_id = searched.group("query", "xhs_id")

        try:
            return await self.parse_explore(f"{xhs_domain}/explore/{query}", xhs_id)
        except Exception as e:
            logger.warning(
                f"parse_explore failed, error: {e}, fallback to parse_discovery"
            )
            return await self.parse_discovery(f"{xhs_domain}/discovery/item/{query}")

    async def parse_explore(self, url: str, xhs_id: str):
        async with self.session.get(url, headers=self.headers) as resp:
            html = await resp.text()
            logger.debug(f"url: {resp.url} | status: {resp.status}")

        if self._is_security_page(str(resp.url), html):
            raise ParseException("小红书访问被拦截（安全校验页），请稍后重试或配置 Cookie/代理")

        json_obj = self._extract_initial_state_json(html)

        # ["note"]["noteDetailMap"][xhs_id]["note"]
        note_data = json_obj.get("note", {}).get("noteDetailMap", {}).get(xhs_id, {}).get("note", {})
        if not note_data:
            raise ParseException("can't find note detail in json_obj")

        class Image(Struct):
            urlDefault: str

        class User(Struct):
            nickname: str
            avatar: str
            userId: str = ""

        class NoteDetail(Struct):
            type: str
            title: str
            desc: str
            user: User
            imageList: list[Image] = field(default_factory=list)
            video: Video | None = None

            @property
            def nickname(self) -> str:
                return self.user.nickname

            @property
            def avatar_url(self) -> str:
                return self.user.avatar

            @property
            def image_urls(self) -> list[str]:
                return [item.urlDefault for item in self.imageList]

            @property
            def video_url(self) -> str | None:
                if self.type != "video" or not self.video:
                    return None
                return self.video.video_url

        note_detail = convert(note_data, type=NoteDetail)

        contents = []
        # 添加视频内容
        if video_url := note_detail.video_url:
            cover_url = self._find_cover_url(note_data)
            contents.append(self._create_video_content(video_url, cover_url))

        # 添加图片内容
        else:
            contents.extend(self._create_gallery_contents(note_data))

        # 构建作者
        author = self.create_author(
            note_detail.nickname,
            note_detail.avatar_url,
            profile_url=self._profile_url(note_detail.user.userId),
        )

        return self.result(
            title=note_detail.title,
            text=note_detail.desc,
            author=author,
            contents=contents,
        )

    async def parse_discovery(self, url: str):
        async with self.session.get(
            url,
            headers=self.ios_headers,
            allow_redirects=True,
        ) as resp:
            html = await resp.text()

        if self._is_security_page(str(resp.url), html):
            raise ParseException("小红书访问被拦截（安全校验页），请稍后重试或配置 Cookie/代理")

        json_obj = self._extract_initial_state_json(html)
        note_data = json_obj.get("noteData")
        if not note_data:
            raise ParseException("can't find noteData in json_obj")
        preload_data = note_data.get("normalNotePreloadData", {})
        raw_note_data = note_data.get("data", {}).get("noteData", {})
        if not raw_note_data:
            raise ParseException("can't find noteData in noteData.data")

        class Image(Struct):
            url: str
            urlSizeLarge: str | None = None

        class User(Struct):
            nickName: str
            avatar: str
            userId: str = ""

        class NoteData(Struct):
            type: str
            title: str
            desc: str
            user: User
            time: int
            lastUpdateTime: int
            imageList: list[Image] = []  # 有水印
            video: Video | None = None

            @property
            def image_urls(self) -> list[str]:
                return [item.url for item in self.imageList]

            @property
            def video_url(self) -> str | None:
                if self.type != "video" or not self.video:
                    return None
                return self.video.video_url

        class NormalNotePreloadData(Struct):
            title: str
            desc: str
            imagesList: list[Image] = []  # 无水印, 但只有一只，用于视频封面

            @property
            def image_urls(self) -> list[str]:
                return [item.urlSizeLarge or item.url for item in self.imagesList]

        note_data = convert(raw_note_data, type=NoteData)

        contents = []
        if video_url := note_data.video_url:
            cover_url = self._find_cover_url(preload_data) or self._find_cover_url(raw_note_data)
            contents.append(self._create_video_content(video_url, cover_url))
        else:
            contents.extend(self._create_gallery_contents(raw_note_data))

        return self.result(
            title=note_data.title,
            author=self.create_author(
                note_data.user.nickName,
                note_data.user.avatar,
                profile_url=self._profile_url(note_data.user.userId),
            ),
            contents=contents,
            text=note_data.desc,
            timestamp=note_data.time // 1000,
        )

    def _extract_initial_state_json(self, html: str) -> dict[str, Any]:
        pattern = r"window\.__INITIAL_STATE__=(.*?)</script>"
        matched = re.search(pattern, html)
        if not matched:
            raise ParseException("小红书分享链接失效或内容已删除")

        json_str = matched.group(1).replace("undefined", "null")
        return json.loads(json_str)

    @staticmethod
    def _profile_url(user_id: str) -> str | None:
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        return f"https://www.xiaohongshu.com/user/profile/{user_id}"

    @staticmethod
    def _is_security_page(final_url: str, html: str) -> bool:
        if "/404/sec_" in final_url:
            return True
        if re.search(r'<div[^>]+class="not-found-container"', html):
            return True
        return False

    @staticmethod
    def _find_cover_url(data: Any) -> str | None:
        """Find the first image-like cover URL from variable XHS video payloads."""
        preferred_keys = ("firstFrameUrl", "coverUrl", "urlSizeLarge", "urlDefault", "url")
        nested_keys = (
            "cover",
            "coverInfo",
            "firstFrame",
            "firstFrameInfo",
            "imageInfo",
            "imageList",
            "imagesList",
            "thumbnail",
        )

        def find(value: Any) -> str | None:
            if isinstance(value, dict):
                for key in preferred_keys:
                    candidate = value.get(key)
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                        return candidate
                for key in nested_keys:
                    if (found := find(value.get(key))) is not None:
                        return found
            elif isinstance(value, list):
                for candidate in value:
                    if (found := find(candidate)) is not None:
                        return found
            return None

        return find(data)

    def _create_gallery_contents(self, note_data: dict[str, Any]):
        """Build mixed static and live-photo galleries while preserving item order."""
        images = note_data.get("imageList")
        if not isinstance(images, list):
            return []

        contents = []
        for image in images:
            if not isinstance(image, dict):
                continue
            image_url = self._original_image_url(image)
            fallback_url = self._image_url(image)
            dynamic_url = self._find_dynamic_url(image)
            if dynamic_url:
                contents.append(self._create_video_content(dynamic_url, fallback_url))
            elif image_url:
                contents.append(ImageContent(self._download_xhs_image(image_url, fallback_url)))
        return contents

    def _create_video_content(self, video_url: str, cover_url: str | None):
        content = self.create_video_content(video_url, headers=self._media_headers())
        if cover_url:
            content.cover = self._download_xhs_image(
                self._original_url(cover_url), self._image_url_from_value(cover_url)
            )
        return content

    async def _download_xhs_image(self, original_url: str, fallback_url: str | None = None):
        """Try XHS original-image CDNs before retaining a displayable fallback."""
        headers = self._media_headers()
        candidates = self._original_image_candidates(original_url)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return await self.downloader.download_img(
                    candidate, headers=headers, proxy=self.proxy
                )
            except Exception as exc:
                last_error = exc

        fallback_url = self._image_url_from_value(fallback_url or "")
        if fallback_url and fallback_url not in candidates:
            logger.warning("XHS original image rejected, using display fallback: %s", last_error)
            return await self.downloader.download_img(
                fallback_url, headers=headers, proxy=self.proxy
            )
        if last_error is not None:
            raise last_error
        raise RuntimeError("XHS image has no usable download URL")

    @staticmethod
    def _original_image_candidates(url: str) -> list[str]:
        """Build the original-image candidates used by XHS's public image CDNs."""
        token = XHSParser._image_token(url)
        if not token:
            return [url]
        candidates = [
            f"https://ci.xiaohongshu.com/{token}?imageView2/format/png",
            *(
                f"https://{domain}/{token}"
                for domain in (
                    "sns-img-bd.xhscdn.com",
                    "sns-img-qc.xhscdn.com",
                    "sns-img-hw.xhscdn.com",
                )
            ),
            url,
        ]
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _image_token(url: str) -> str | None:
        path = urllib.parse.urlsplit(url).path.strip("/")
        if not path:
            return None
        parts = path.split("/")
        if len(parts) >= 4:
            return "/".join(parts[2:])
        return parts[-1] if len(parts[-1]) > 10 else None

    def _media_headers(self) -> dict[str, str]:
        return {**self.headers, "Referer": "https://www.xiaohongshu.com/"}

    @staticmethod
    def _original_image_url(image: dict[str, Any]) -> str | None:
        """Use the original image URL instead of XHS's watermarked `!` variant."""
        url = XHSParser._image_url(image)
        return XHSParser._original_url(url) if url else None

    @staticmethod
    def _image_url(image: dict[str, Any]) -> str | None:
        for key in ("urlDefault", "url", "urlSizeLarge", "urlPre"):
            value = image.get(key)
            if url := XHSParser._image_url_from_value(value):
                return url
        return None

    @staticmethod
    def _original_url(url: str) -> str:
        return url.split("!", 1)[0]

    @staticmethod
    def _image_url_from_value(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        url = html.unescape(value).strip()
        if url.startswith("//"):
            url = f"https:{url}"
        if url.startswith("http://"):
            url = f"https://{url.removeprefix('http://')}"
        return url if url.startswith("https://") else None

    @staticmethod
    def _find_dynamic_url(data: dict[str, Any]) -> str | None:
        """Extract a Live Photo/video URL without mistaking its still image for media."""
        for key in ("livePhotoUrl", "live_photo_url"):
            if url := XHSParser._media_url(data.get(key)):
                return url

        containers = [data]
        for key in ("livePhoto", "livePhotoInfo", "video", "videoInfo"):
            value = data.get(key)
            if isinstance(value, dict):
                containers.append(value)

        for container in containers:
            for key in ("playUrl", "videoUrl", "masterUrl"):
                if url := XHSParser._media_url(container.get(key)):
                    return url
            stream = container.get("stream")
            if not isinstance(stream, dict):
                continue
            for codec in ("h265", "h264", "av1", "h266"):
                stream_items = stream.get(codec)
                if not isinstance(stream_items, list):
                    continue
                for item in stream_items:
                    if not isinstance(item, dict):
                        continue
                    for key in ("masterUrl", "playUrl", "videoUrl", "url", "backupUrl"):
                        if url := XHSParser._media_url(item.get(key)):
                            return url
                    backup_urls = item.get("backupUrls")
                    if isinstance(backup_urls, list):
                        for value in backup_urls:
                            if url := XHSParser._media_url(value):
                                return url
        return None

    @staticmethod
    def _media_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        url = html.unescape(value).strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url if url.startswith(("http://", "https://")) else None


class Stream(Struct):
    h264: list[dict[str, Any]] | None = None
    h265: list[dict[str, Any]] | None = None
    av1: list[dict[str, Any]] | None = None
    h266: list[dict[str, Any]] | None = None


class Media(Struct):
    stream: Stream


class Video(Struct):
    media: Media

    @property
    def video_url(self) -> str | None:
        stream = self.media.stream

        # h264 有水印，h265 无水印
        if stream.h265:
            return stream.h265[0]["masterUrl"]
        elif stream.h264:
            return stream.h264[0]["masterUrl"]
        elif stream.av1:
            return stream.av1[0]["masterUrl"]
        elif stream.h266:
            return stream.h266[0]["masterUrl"]
        return None
