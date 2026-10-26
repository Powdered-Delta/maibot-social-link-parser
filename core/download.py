from asyncio import Task, TimeoutError, create_task, gather, sleep, to_thread
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import random
import aiofiles
import yt_dlp
from aiohttp import ClientError, ClientSession, ClientTimeout
from msgspec import Struct, convert
from tqdm.asyncio import tqdm

from astrbot.api import logger

from . import ffmpeg
from .config import PluginConfig
from .constants import COMMON_HEADER
from .exception import (
    DownloadException,
    DurationLimitException,
    LinkExpiredException,
    ParseException,
    SizeLimitException,
    ZeroSizeException,
)
from .utils import LimitedSizeDict, generate_file_name, merge_av, safe_unlink

P = ParamSpec("P")
T = TypeVar("T")
DownloadProgressCallback = Callable[[int, int | None], None]
_download_progress_callback: ContextVar[DownloadProgressCallback | None] = ContextVar(
    "download_progress_callback", default=None
)


def set_download_progress_callback(callback: DownloadProgressCallback | None) -> Token[DownloadProgressCallback | None]:
    """Attach a progress observer to downloads created in the current task context."""
    return _download_progress_callback.set(callback)


def reset_download_progress_callback(token: Token[DownloadProgressCallback | None]) -> None:
    _download_progress_callback.reset(token)


def _ffmpeg_location() -> str | None:
    """yt-dlp 的 ffmpeg_location：指向自举 ffmpeg 所在目录，便于其发现 ffmpeg。"""
    path = ffmpeg.configured_ffmpeg()
    return str(path.parent) if path else None


def auto_task(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Task[T]]:
    """装饰器：自动将异步函数调用转换为 Task, 完整保留类型提示"""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Task[T]:
        coro = func(*args, **kwargs)
        name = " | ".join(str(arg) for arg in args if isinstance(arg, str))
        return create_task(coro, name=func.__name__ + " | " + name)

    return wrapper


class VideoInfo(Struct):
    title: str
    """标题"""
    channel: str
    """频道名称"""
    uploader: str
    """上传者 id"""
    duration: int
    """时长"""
    timestamp: int
    """发布时间戳"""
    thumbnail: str
    """封面图片"""
    description: str
    """简介"""
    channel_id: str
    """频道 id"""

    @property
    def author_name(self) -> str:
        return f"{self.channel}@{self.uploader}"


class Downloader:
    """下载器，支持youtube-dlp 和 流式下载"""

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.max_size = self.cfg.source_max_size
        self.default_headers: dict[str, str] = COMMON_HEADER.copy()
        # 视频信息缓存
        self.info_cache: LimitedSizeDict[str, VideoInfo] = LimitedSizeDict()
        # 用于流式下载的客户端
        self.client = ClientSession(
            timeout=ClientTimeout(total=self.cfg.download_timeout)
        )

    async def close(self):
        """关闭网络客户端"""
        await self.client.close()

    @auto_task
    async def streamd(
        self,
        url: str,
        *,
        file_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> Path:
        """流式下载"""
        if not file_name:
            file_name = generate_file_name(url)
        file_path = self.cfg.cache_dir / file_name
        part_path = file_path.with_suffix(file_path.suffix + ".part")
        # 如果文件存在，则直接返回
        if file_path.exists():
            return file_path
        headers = headers or self.default_headers
        retries = self.cfg.download_retry_times
        for attempt in range(retries + 1):
            try:
                existing_size = part_path.stat().st_size if part_path.exists() else 0
                request_headers = headers.copy()
                if existing_size:
                    request_headers["Range"] = f"bytes={existing_size}-"
                async with self.client.get(
                    url, headers=request_headers, allow_redirects=True, proxy=proxy
                ) as response:
                    if response.status in (401, 403, 410):
                        # 抖音等平台的 CDN 直链带防盗链签名。签名被拒时重试同一个
                        # URL 只会一直 403，必须由上层重新解析原链接取新直链。
                        raise LinkExpiredException(
                            f"媒体直链被拒绝: HTTP {response.status} {response.reason}"
                        )
                    if response.status >= 400:
                        raise ClientError(f"HTTP {response.status} {response.reason}")
                    if existing_size and response.status != 206:
                        # The server ignored Range. Restart rather than appending a full response.
                        await safe_unlink(part_path)
                        existing_size = 0
                    content_length = response.content_length
                    total_size = (content_length + existing_size) if content_length else None
                    max_bytes = self.max_size * 1024 * 1024

                    if content_length == 0:
                        logger.warning(f"媒体 url: {url}, 大小为 0, 取消下载")
                        raise ZeroSizeException
                    if total_size and total_size > max_bytes:
                        logger.warning(
                            f"媒体 url: {url} 大小 {total_size / 1024 / 1024:.2f} MB 超过 {self.max_size} MB, 取消下载"
                        )
                        raise SizeLimitException

                    downloaded = existing_size
                    if callback := _download_progress_callback.get():
                        callback(downloaded, total_size)
                    with self.get_progress_bar(file_name, total_size) as bar:
                        if existing_size:
                            bar.update(existing_size)
                        async with aiofiles.open(part_path, "ab" if existing_size else "wb") as file:
                            async for chunk in response.content.iter_chunked(
                                1024 * 1024
                            ):
                                downloaded += len(chunk)
                                if callback := _download_progress_callback.get():
                                    callback(downloaded, total_size)
                                if downloaded > max_bytes:
                                    raise SizeLimitException
                                await file.write(chunk)
                                bar.update(len(chunk))

                    if downloaded == 0:
                        logger.warning(f"媒体 url: {url}, 实际大小为 0, 取消下载")
                        raise ZeroSizeException
                    if total_size and downloaded < total_size:
                        raise ClientError(
                            f"HTTP payload incomplete {downloaded}/{total_size}"
                        )

                part_path.replace(file_path)
                return file_path
            except (ZeroSizeException, SizeLimitException):
                await safe_unlink(file_path)
                await safe_unlink(part_path)
                raise
            except LinkExpiredException:
                # 不做退避重试：同一签名 URL 再请求也不会成功。
                await safe_unlink(part_path)
                raise
            except (ClientError, TimeoutError, OSError) as exc:
                if attempt < retries:
                    # Keep the partial file so the next attempt can resume with a
                    # Range request instead of re-downloading from scratch.
                    delay = 2**attempt + random.uniform(0, 0.5)
                    logger.warning(
                        "下载中断，%s 秒后重试 (%s/%s) | url: %s, error: %s",
                        round(delay, 1),
                        attempt + 1,
                        retries,
                        url,
                        exc,
                    )
                    await sleep(delay)
                    continue
                # Retain .part on final transient failure: a later confirmation can
                # resume from the retained bytes rather than discarding progress.
                logger.exception(f"下载失败 | url: {url}, file_path: {file_path}")
                raise DownloadException("媒体下载失败") from exc
        raise DownloadException("媒体下载失败")

    @staticmethod
    def get_progress_bar(desc: str, total: int | None = None) -> tqdm:
        """获取进度条 bar

        Args:
            desc (str): 描述
            total (int | None): 总大小. Defaults to None.

        Returns:
            tqdm: 进度条
        """
        return tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
            colour="green",
            desc=desc,
            disable=True,
        )

    @auto_task
    async def download_video(
        self,
        url: str,
        *,
        video_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        if video_name is None:
            video_name = generate_file_name(url, ".mp4")
        return await self.streamd(
            url, file_name=video_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_audio(
        self,
        url: str,
        *,
        audio_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        if audio_name is None:
            audio_name = generate_file_name(url, ".mp3")
        return await self.streamd(
            url, file_name=audio_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_file(
        self,
        url: str,
        *,
        file_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> Path:
        if file_name is None:
            file_name = generate_file_name(url, ".zip")
        return await self.streamd(
            url, file_name=file_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_img(
        self,
        url: str,
        *,
        img_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> Path:
        if img_name is None:
            img_name = generate_file_name(url, ".jpg")
        return await self.streamd(url, file_name=img_name, headers=headers, proxy=proxy)

    async def download_imgs_without_raise(
        self,
        urls: list[str],
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> list[Path]:
        paths_or_errs = await gather(
            *[self.download_img(url, headers=headers, proxy=proxy) for url in urls],
            return_exceptions=True,
        )
        return [p for p in paths_or_errs if isinstance(p, Path)]

    @auto_task
    async def download_av_and_merge(
        self,
        v_url: str,
        a_url: str,
        *,
        output_path: Path,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        """
        download video and audio file by url with stream and merge
        """
        v_path, a_path = await gather(
            self.download_video(v_url, headers=headers, proxy=proxy),
            self.download_audio(a_url, headers=headers, proxy=proxy),
        )
        await merge_av(v_path=v_path, a_path=a_path, output_path=output_path)
        return output_path

    async def ytdlp_extract_info(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> VideoInfo:
        if (info := self.info_cache.get(url)) is not None:
            return info
        opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if format:
            opts["format"] = format
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            raw = await to_thread(ydl.extract_info, url, download=False)
            if not raw:
                raise ParseException("获取视频信息失败")
        info = convert(raw, VideoInfo)
        self.info_cache[url] = info
        return info

    async def ytdlp_extract_raw(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> dict[str, Any]:
        opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if format:
            opts["format"] = format

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            raw = await to_thread(ydl.extract_info, url, download=False)
            if not isinstance(raw, dict):
                raise ParseException("yt-dlp 返回数据异常")
            return raw  # type: ignore

    @auto_task
    async def ytdlp_download_video(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
        node: bool = False,
    ) -> Path:
        info = await self.ytdlp_extract_info(
            url, cookiefile=cookiefile, headers=headers, proxy=proxy
        )
        if info.duration > self.cfg.max_duration:
            raise DurationLimitException

        video_path = self.cfg.cache_dir / generate_file_name(url, ".mp4")
        if video_path.exists():
            return video_path

        opts = {
            "outtmpl": str(video_path),
            "merge_output_format": "mp4",
            # "format": f"bv[filesize<={info.duration // 10 + 10}M]+ba/b[filesize<={info.duration // 8 + 10}M]",
            # "format": "bv*[height<=720]+ba/b[height<=720]",
            "format": format or "best",
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
            "http_headers": headers or self.default_headers,
        }
        if (location := _ffmpeg_location()):
            opts["ffmpeg_location"] = location
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if node:
            opts["js_runtimes"] = {"node": {}}

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await to_thread(ydl.download, [url])
        return video_path

    @auto_task
    async def ytdlp_download_video_relaxed(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
        node: bool = False,
    ) -> Path:
        file_stem = generate_file_name(url)
        video_path = self.cfg.cache_dir / f"{file_stem}.mp4"
        if video_path.exists():
            return video_path

        opts = {
            "outtmpl": str(self.cfg.cache_dir / file_stem) + ".%(ext)s",
            "merge_output_format": "mp4",
            "format": format or None,
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
            "http_headers": headers or self.default_headers,
            "quiet": True,
            "no_warnings": True,
        }
        if (location := _ffmpeg_location()):
            opts["ffmpeg_location"] = location
        if not opts["format"]:
            opts.pop("format")
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if node:
            opts["js_runtimes"] = {"node": {}}

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await to_thread(ydl.download, [url])
        if video_path.exists():
            return video_path

        candidates = sorted(self.cfg.cache_dir.glob(f"{file_stem}*.mp4"))
        if candidates:
            return candidates[0]
        raise DownloadException("yt-dlp 视频下载失败")

    @auto_task
    async def ytdlp_download_audio(
        self,
        url: str,
        *,
        cookiefile: Path | None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> Path:
        file_name = generate_file_name(url)
        audio_path = self.cfg.cache_dir / f"{file_name}.flac"
        if audio_path.exists():
            return audio_path

        opts = {
            "outtmpl": str(self.cfg.cache_dir / file_name) + ".%(ext)s",
            "format": format or "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "flac",
                    "preferredquality": "0",
                }
            ],
            "cookiefile": None,
            "http_headers": headers or self.default_headers,
        }
        if (location := _ffmpeg_location()):
            opts["ffmpeg_location"] = location
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await to_thread(ydl.download, [url])
        return audio_path
