# -*- coding: utf-8 -*-
"""解析逻辑与重试冗余测试。

- 小红书链接识别：无查询串的 /explore/<id> 与带 xsec_token 的分享链接都要命中。
- 抖音 CDN 直链选择的确定性：不再因随机选源导致下载不稳定。
- BaseParser.parse 与 ffmpeg 自举下载的瞬时网络错误重试。

需要解析器运行依赖（aiohttp/msgspec/curl_cffi/bilibili-api）。缺依赖时跳过。
"""

from __future__ import annotations

import asyncio
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("msgspec")
pytest.importorskip("curl_cffi")
pytest.importorskip("bilibili_api")

from maibot_social_link_parser.core import ffmpeg  # noqa: E402
from maibot_social_link_parser.core.parsers.base import BaseParser  # noqa: E402
from maibot_social_link_parser.core.parsers.douyin.video import _prefer_url  # noqa: E402
from maibot_social_link_parser.core.parsers.xhs import XHSParser  # noqa: E402


XHS_ID = "68e8e3fa00000000030342ec"


class TestXhsUrlRecognition:
    def test_bare_explore_link_matches(self) -> None:
        """无查询串的 /explore/<id> 也必须命中，否则会被误判为无法解析。"""
        keyword, matched = XHSParser.search_url(f"https://www.xiaohongshu.com/explore/{XHS_ID}")
        assert keyword == "xiaohongshu.com"
        assert matched.group("xhs_id") == XHS_ID
        assert matched.group("query") is None

    def test_share_link_with_token_matches(self) -> None:
        url = f"https://www.xiaohongshu.com/explore/{XHS_ID}?xsec_token=ABC%3D%3D&type=normal"
        _, matched = XHSParser.search_url(url)
        assert matched.group("xhs_id") == XHS_ID
        assert matched.group("query") == "xsec_token=ABC%3D%3D&type=normal"

    def test_discovery_item_link_matches(self) -> None:
        url = f"https://www.xiaohongshu.com/discovery/item/{XHS_ID}?xsec_source=app_share"
        _, matched = XHSParser.search_url(url)
        assert matched.group("xhs_id") == XHS_ID

    def test_xhslink_short_link_matches(self) -> None:
        keyword, matched = XHSParser.search_url("https://xhslink.cn/aBcDeF")
        assert keyword == "xhslink.cn"
        assert matched.group(0) == "xhslink.cn/aBcDeF"


class TestDouyinCdnSelection:
    def test_prefers_first_https(self) -> None:
        # 抖音为同一素材返回多个 CDN 镜像；随机选源会让下载结果不稳定。
        assert _prefer_url(["http://a/1", "https://b/2", "https://c/3"]) == "https://b/2"

    def test_falls_back_to_first_usable(self) -> None:
        assert _prefer_url(["http://only/1"]) == "http://only/1"

    def test_empty_list_returns_default(self) -> None:
        assert _prefer_url([], "fallback") == "fallback"

    def test_selection_is_deterministic(self) -> None:
        urls = ["http://a", "https://b", "https://c"]
        assert _prefer_url(urls) == _prefer_url(urls) == _prefer_url(urls)


class _BoomHandler:
    """A parse handler that fails with a transient error N times before succeeding."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def __call__(self, parser, searched):
        from aiohttp import ClientError

        self.calls += 1
        if self.calls <= self.fail_times:
            raise ClientError("transient")
        return "ok"


class _FakeConfig:
    download_retry_times = 3
    common_timeout = 5
    proxy = None
    data_dir = Path(".")
    parser = None


class _RetryParser(BaseParser):
    """Minimal BaseParser carrying a stubbed handler and config for retry tests."""

    from maibot_social_link_parser.core.data import Platform

    platform = Platform(name="xhs", display_name="test")

    def __init__(self, fail_times: int) -> None:
        self.cfg = _FakeConfig()
        self._handlers = {"k": _BoomHandler(fail_times)}


class TestParseRetry:
    def test_transient_error_is_retried_then_succeeds(self) -> None:
        parser = _RetryParser(fail_times=2)
        assert asyncio.run(parser.parse("k", None)) == "ok"
        assert parser._handlers["k"].calls == 3

    def test_gives_up_after_retry_budget(self) -> None:
        parser = _RetryParser(fail_times=10)
        with pytest.raises(Exception):
            asyncio.run(parser.parse("k", None))
        assert parser._handlers["k"].calls == 4  # 1 initial + 3 retries


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFfmpegDownloadRetry:
    def test_retries_then_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        attempts = {"count": 0}

        def _flaky_urlopen(request, timeout=0):
            attempts["count"] += 1
            if attempts["count"] < ffmpeg.DOWNLOAD_RETRY_TIMES:
                raise urllib.error.URLError("connection reset")
            return _Response(b"binary-bytes")

        monkeypatch.setattr(ffmpeg.urllib.request, "urlopen", _flaky_urlopen)
        dest = tmp_path / "archive"
        ffmpeg._download_to("https://example.invalid/ffmpeg.tar.xz", dest)
        assert attempts["count"] == ffmpeg.DOWNLOAD_RETRY_TIMES
        assert dest.read_bytes() == b"binary-bytes"

    def test_raises_after_budget_and_cleans_partial(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _always_fail(request, timeout=0):
            raise urllib.error.URLError("host unreachable")

        monkeypatch.setattr(ffmpeg.urllib.request, "urlopen", _always_fail)
        dest = tmp_path / "archive"
        with pytest.raises(urllib.error.URLError):
            ffmpeg._download_to("https://example.invalid/ffmpeg.tar.xz", dest)
        assert not dest.exists()


class TestSendRetry:
    """发送路径的瞬时失败重试（OneBot HTTP 与 NapCat WebSocket）。"""

    def test_http_retries_5xx_then_succeeds(self) -> None:
        from maibot_social_link_parser import sender

        attempts = {"count": 0}

        async def flaky():
            attempts["count"] += 1
            if attempts["count"] < sender.SEND_RETRY_TIMES + 1:
                raise RuntimeError("temporary 502")
            return True

        assert asyncio.run(sender._with_retry(flaky, label="test")) is True
        assert attempts["count"] == sender.SEND_RETRY_TIMES + 1

    def test_http_gives_up_after_budget(self) -> None:
        from maibot_social_link_parser import sender

        attempts = {"count": 0}

        async def always_fail():
            attempts["count"] += 1
            raise RuntimeError("still down")

        assert asyncio.run(sender._with_retry(always_fail, label="test")) is False
        assert attempts["count"] == sender.SEND_RETRY_TIMES + 1
