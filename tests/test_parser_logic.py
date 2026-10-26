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


class TestForwardImageSharing:
    """合并转发不应 base64 内联整包图集：那会把几十 MB 压进一帧导致整单失败。

    真机事故：7 节点图集 1.75MB 图片 → base64 ≈2.35MB 单帧，Platform IO 直接
    DeliveryStatus.FAILED（error 为空），QQ 侧表现为“图片已过期”。
    """

    def test_shared_root_child_is_shadowable(self) -> None:
        from maibot_social_link_parser import sender

        # 共享根下的路径应被识别为 NapCat 可读，无需 base64。
        # 返回值统一成 POSIX 形式（Linux 容器内即原生路径）。
        assert (
            sender._shared_path_for(Path(sender.SHARED_DATA_ROOT) / "a.webp")
            == f"{sender.SHARED_DATA_ROOT}/a.webp"
        )

    def test_shared_root_itself_is_accepted(self) -> None:
        from maibot_social_link_parser import sender

        assert sender._shared_path_for(Path(sender.SHARED_DATA_ROOT)) == sender.SHARED_DATA_ROOT

    def test_sibling_prefix_is_not_shared(self, tmp_path: Path) -> None:
        """`/MaiMBot/data/pluginsX` 不能因为前缀相同被误判为共享卷内。"""
        from maibot_social_link_parser import sender

        lookalike = Path(sender.SHARED_DATA_ROOT + "X") / "a.webp"
        assert sender._shared_path_for(lookalike) is None

    def test_path_outside_shared_root_is_not_shared(self, tmp_path: Path) -> None:
        from maibot_social_link_parser import sender

        assert sender._shared_path_for(tmp_path / "a.webp") is None

    def test_small_image_inlined_when_outside_shared_root(self, tmp_path: Path) -> None:
        from maibot_social_link_parser import sender

        image = tmp_path / "small.webp"
        image.write_bytes(b"x" * 1024)
        segment = sender._forward_image_segment(image)
        assert segment is not None
        # 非共享路径且体积小 → 回退内联，仍是 imageurl 段以便适配器编码
        assert segment["type"] == "imageurl"
        assert segment["data"]["data"]["url"].startswith("base64://")

    def test_oversized_image_outside_shared_root_is_skipped(self, tmp_path: Path) -> None:
        from maibot_social_link_parser import sender

        image = tmp_path / "huge.webp"
        image.write_bytes(b"x" * (sender.INLINE_IMAGE_MAX_BYTES + 1))
        # 超限时宁可不发这一张，也不要撑爆整帧导致整单失败
        assert sender._forward_image_segment(image) is None

    def test_missing_image_is_skipped(self, tmp_path: Path) -> None:
        from maibot_social_link_parser import sender

        assert sender._forward_image_segment(tmp_path / "nope.webp") is None

    def test_shared_image_payload_is_nested(self, tmp_path: Path) -> None:
        """共享卷图片必须是嵌套的 imageurl，不能是扁平的 data.url。

        扁平结构经 Host 后会变成 DictComponent，内层 ``type`` 丢失，适配器
        ``_build_dict_component_segment`` 读不到 imageurl，于是降级成
        ``[unsupported:dict]`` 直接发到 QQ —— 真机表现就是一片
        ``[unsupported:dict]`` 占位。
        """
        from maibot_social_link_parser import sender

        image = Path(sender.SHARED_DATA_ROOT) / "demo.webp"
        # 直接调私有构造器，避免依赖共享卷在测试机上真实存在
        segment = sender._nested_imageurl_segment(f"file://{image}")

        assert segment["type"] == "imageurl"
        inner = segment["data"]
        assert inner.get("type") == "imageurl", "内层缺少 type，Host 收成 DictComponent 后会丢类型"
        assert inner["data"]["url"] == f"file://{image}"

    def test_flat_payload_would_lose_its_type(self) -> None:
        """反例：扁平 payload 里没有 type，这正是 [unsupported:dict] 的来源。"""
        from maibot_social_link_parser import sender

        nested = sender._nested_imageurl_segment("file:///tmp/a.webp")
        flat = {"type": "imageurl", "data": {"url": "file:///tmp/a.webp"}}

        assert nested["data"].get("type"), "嵌套形状必须自带内层 type"
        assert not flat["data"].get("type"), "扁平形状没有内层 type，适配器无法识别"


class TestForwardNodePayloadShape:
    """转发节点里每段的键名必须匹配适配器构造器的读取方式。

    适配器 ``QQOutboundSegmentEncoder``：
      - text 读 ``item["data"]``（字符串），不是 ``content``
      - image 只读 ``binary_data_base64``
      - imageurl 读 ``data["url"]`` 或 ``data["file"]``
    用错键名不会报错，只会退化成 ``[image]`` 或空文本。
    """

    def test_text_segment_uses_data_key(self) -> None:
        from maibot_social_link_parser import sender

        segment = sender.text_segment("标题：测试")
        assert segment["data"]["text"] == "标题：测试"

    def test_image_segment_keeps_file_uri_in_data(self) -> None:
        from maibot_social_link_parser import sender

        segment = sender.image_segment(Path("/tmp/a.webp"))
        assert segment["data"]["file"].startswith("file://")
