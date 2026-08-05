# -*- coding: utf-8 -*-
"""入口检查逻辑测试。

需要 MaiBot Runner 环境（含 maibot_sdk、pydantic 及解析器依赖）。
本地无 SDK 时会以 pytest.skip 跳过。
"""

from __future__ import annotations

import pytest

maibot_sdk = pytest.importorskip("maibot_sdk")

from plugin import MultiPlatformParserPlugin, PendingDownload, PluginConfig

from . import entry_check_data as data


def make_plugin() -> MultiPlatformParserPlugin:
    plugin = MultiPlatformParserPlugin()
    plugin.set_plugin_config({"plugin": {"enabled": True, "config_version": "0.3.6"}})
    assert isinstance(plugin.config, PluginConfig)
    return plugin


def enable_advanced_parser(plugin: MultiPlatformParserPlugin, **overrides) -> None:
    base = {
        "enabled": True,
        "group_whitelist": [],
        "group_blacklist": [],
        "user_whitelist": [],
        "user_blacklist": [],
        "private_whitelist": [],
        "private_blacklist": [],
        "download_intent_regex": "",
    }
    base.update(overrides)
    plugin.config.parser_advanced = PluginConfig().parser_advanced.model_copy(update=base)


class TestIsAllowedMessage:
    """黑/白名单入口检查（仅高级分组开启时生效）。"""

    def test_all_allowed_when_advanced_disabled(self) -> None:
        plugin = make_plugin()
        assert plugin._is_allowed_message(data.with_text(data.GROUP_ALLOWED, "你好"))
        assert plugin._is_allowed_message(data.with_text(data.GROUP_BLOCKED, "你好"))
        assert plugin._is_allowed_message(data.PRIVATE_BLOCKED)
        assert plugin._is_allowed_message(data.PRIVATE_OTHER)

    def test_group_blacklist_wins(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, group_blacklist=["999001"])
        assert not plugin._is_allowed_message(data.with_text(data.GROUP_BLOCKED, "你好"))
        assert plugin._is_allowed_message(data.with_text(data.GROUP_ALLOWED, "你好"))
        assert plugin._is_allowed_message(data.with_text(data.GROUP_OTHER, "你好"))

    def test_group_whitelist_restricts(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, group_whitelist=["999002"])
        assert plugin._is_allowed_message(data.with_text(data.GROUP_ALLOWED, "你好"))
        assert not plugin._is_allowed_message(data.with_text(data.GROUP_OTHER, "你好"))

    def test_group_whitelist_overlaps_blacklist_black_wins(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, group_whitelist=["999001"], group_blacklist=["999001"])
        assert not plugin._is_allowed_message(data.with_text(data.GROUP_BLOCKED, "你好"))

    def test_user_blacklist_wins(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, user_blacklist=["666001"])
        assert not plugin._is_allowed_message(data.USER_BLOCKED)
        assert plugin._is_allowed_message(data.USER_ALLOWED)
        assert plugin._is_allowed_message(data.USER_OTHER)

    def test_user_whitelist_restricts(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, user_whitelist=["666002"])
        assert plugin._is_allowed_message(data.USER_ALLOWED)
        assert not plugin._is_allowed_message(data.USER_OTHER)

    def test_private_blacklist_blocks_private_chat(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, private_blacklist=["777001"])
        assert not plugin._is_allowed_message(data.PRIVATE_BLOCKED)
        assert plugin._is_allowed_message(data.PRIVATE_ALLOWED)
        assert plugin._is_allowed_message(data.PRIVATE_OTHER)

    def test_private_whitelist_restricts_private_chat(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, private_whitelist=["777002"])
        assert plugin._is_allowed_message(data.PRIVATE_ALLOWED)
        assert not plugin._is_allowed_message(data.PRIVATE_OTHER)

    def test_whitelist_empty_means_allow_all(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, group_whitelist=[], private_whitelist=[], user_whitelist=[])
        assert plugin._is_allowed_message(data.with_text(data.GROUP_OTHER, "你好"))
        assert plugin._is_allowed_message(data.PRIVATE_OTHER)


class TestDownloadConfirmation:
    def test_default_intents(self) -> None:
        plugin = make_plugin()
        for text in ("下载", "下载 1", "下载 2"):
            assert plugin._is_download_confirmation(text), text

    def test_non_intent_text_rejected(self) -> None:
        plugin = make_plugin()
        for text in (
            "嗯嗯",
            "这个视频真好看",
            "今天下载量不错",
            "请下载",
            "帮我下载",
            "发视频",
            "保存",
            "这个视频怎么下载？",
            "我想下载这个视频",
            "download",
        ):
            assert not plugin._is_download_confirmation(text), text

    def test_custom_regex_merged(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, download_intent_regex=r"下載|down ?load")
        assert plugin._is_download_confirmation("下載")
        assert plugin._is_download_confirmation("download")
        assert plugin._is_download_confirmation("down load")
        assert plugin._is_download_confirmation("下载")

    def test_custom_regex_ignored_when_disabled(self) -> None:
        plugin = make_plugin()
        plugin.config.parser_advanced = PluginConfig().parser_advanced  # enabled=False
        assert not plugin._is_download_confirmation("下載")
        assert plugin._is_download_confirmation("下载")

    def test_link_and_download_in_same_message_is_a_direct_request(self) -> None:
        plugin = make_plugin()
        for text in (
            "下载\n4.61 复制打开抖音，看看【MFX的作品】陆行虎鲸生气时的样子 https://v.douyin.com/iKQUW_bRew0/ T@l.pq 04/05 :2pm vSl:/",
            "4.61 复制打开抖音，看看【MFX的作品】陆行虎鲸生气时… https://v.douyin.com/iKQUW_bRew0/\n下载",
        ):
            assert plugin._has_download_command(text), text
            assert plugin._extract_urls(text), text

    def test_download_word_in_normal_message_does_not_trigger(self) -> None:
        plugin = make_plugin()
        for text in (
            "我想下载这个视频",
            "这个视频怎么下载？",
            "下载速度好慢",
            "视频下载不了怎么办",
        ):
            assert not plugin._has_download_command(text), text

    def test_download_with_no_link_only_confirms_existing_pending_item(self) -> None:
        plugin = make_plugin()
        text = "4.61 复制打开抖音，看看【MFX的作品】陆行虎鲸生气时…\n下载"
        assert plugin._has_download_command(text)
        assert not plugin._extract_urls(text)

    def test_exact_download_command_is_detected_from_message_source(self) -> None:
        plugin = make_plugin()
        message = data.download_confirm_message()
        own_source = plugin._message_source(message, include_reply=False)
        assert own_source == "下载"
        assert plugin._has_download_command(own_source)


class TestExtractUrls:
    def test_plain_bilibili_link(self) -> None:
        plugin = make_plugin()
        urls = plugin._extract_urls(data.bilibili_video_link_message()["processed_plain_text"])
        assert "https://www.bilibili.com/video/BV1xx411c7mD/?p=2" in urls

    def test_b23_short_link(self) -> None:
        plugin = make_plugin()
        urls = plugin._extract_urls(data.bilibili_short_link_message()["processed_plain_text"])
        assert any(u.startswith("https://b23.tv/") for u in urls)

    def test_douyin_and_xhs(self) -> None:
        plugin = make_plugin()
        assert plugin._extract_urls(data.douyin_short_link_message()["processed_plain_text"]) == [
            "https://v.douyin.com/xxxxx/"
        ]
        assert "https://www.xiaohongshu.com/explore/123456" in plugin._extract_urls(
            data.xhs_link_message()["processed_plain_text"]
        )


class TestMessageSource:
    """_message_source 应综合纯文本、消息段和平台卡片负载提取链接。"""

    def test_miniapp_card_url_restored(self) -> None:
        plugin = make_plugin()
        source = plugin._message_source(data.bilibili_miniapp_card_message())
        assert "https://www.bilibili.com/video/BV1xx411c7mD/" in source
        assert "https://i0.hdslb.com/bfs/archive/cover.jpg" in source

    def test_miniapp_card_b23_shortlink_with_download_intent(self) -> None:
        plugin = make_plugin()
        message = data.bilibili_miniapp_card_shortlink_message()
        source = plugin._message_source(message)
        assert "https://b23.tv/pGGBsrB" in source
        assert plugin._has_download_command(plugin._message_source(message, include_reply=False))
        pending, choices, queue_size, error = plugin._consume_pending_download(message, source)
        assert pending is None and not choices and queue_size == 0 and error is None

    def test_top_level_card_payload_url_restored(self) -> None:
        plugin = make_plugin()
        message = data.bilibili_miniapp_card_shortlink_message()
        message["platform_card_payloads"] = message["message_info"]["additional_config"].pop(
            "platform_card_payloads"
        )
        assert "https://b23.tv/pGGBsrB" in plugin._message_source(message)

    def test_no_card_no_false_positive(self) -> None:
        plugin = make_plugin()
        source = plugin._message_source(data.plain_chat_message())
        assert "bilibili" not in source
        assert "https://" not in source

    def test_raw_text_link_kept(self) -> None:
        plugin = make_plugin()
        source = plugin._message_source(data.bilibili_video_link_message())
        assert "https://www.bilibili.com/video/BV1xx411c7mD/?p=2" in source


class TestPendingDownloadReply:
    @staticmethod
    def pending(url: str, title: str) -> PendingDownload:
        return PendingDownload(
            parser=None,
            keyword="",
            searched=None,
            url=url,
            reference_urls=(url,),
            title=title,
            expires_at=2_000_000_000,
        )

    def test_reply_selects_the_referenced_pending_item(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="10001")
        key = (plugin._session_id(message), "10001")
        first = self.pending("https://v.douyin.com/video-a", "视频 A")
        second = self.pending("https://v.douyin.com/video-b", "视频 B")
        plugin._pending_downloads[key] = [first, second]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is first
        assert not choices and queue_size == 2 and error is None
        assert plugin._pending_downloads[key] == [first, second]

    def test_replying_to_another_users_overview_never_downloads_latest_item(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        key = (plugin._session_id(message), "20002")
        own_latest = self.pending("https://v.douyin.com/video-b", "视频 B")
        plugin._pending_downloads[key] = [own_latest]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 1
        assert error == "引用的详情卡片不属于你的待下载内容，无法下载。"
        assert plugin._pending_downloads[key] == [own_latest]

    def test_group_allowlist_can_download_another_users_referenced_item(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, cross_sender_download_group_whitelist=["999001"])
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        owner_key = (plugin._session_id(message), "10001")
        original = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[owner_key] = [original]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is original
        assert not choices and queue_size == 1 and error is None
        assert plugin._pending_downloads[owner_key] == [original]

    def test_user_allowlist_can_download_another_users_referenced_item(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, cross_sender_download_user_whitelist=["20002"])
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        owner_key = (plugin._session_id(message), "10001")
        original = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[owner_key] = [original]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is original
        assert not choices and queue_size == 1 and error is None

    def test_blacklist_mode_allows_unblocked_user(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, cross_sender_download_access_mode="blacklist")
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        owner_key = (plugin._session_id(message), "10001")
        original = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[owner_key] = [original]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is original
        assert not choices and queue_size == 1 and error is None

    def test_blacklist_mode_blocks_listed_user(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(
            plugin,
            cross_sender_download_access_mode="blacklist",
            cross_sender_download_user_blacklist=["20002"],
        )
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        owner_key = (plugin._session_id(message), "10001")
        original = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[owner_key] = [original]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 0
        assert error == "引用的详情卡片不属于你的待下载内容，无法下载。"

    def test_delegate_user_global_blacklist_does_not_block_authorized_quote(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(
            plugin,
            user_blacklist=["20002"],
            cross_sender_download_user_whitelist=["20002"],
        )
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        owner_key = (plugin._session_id(message), "10001")
        original = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[owner_key] = [original]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is original
        assert not choices and queue_size == 1 and error is None

    def test_global_blacklisted_delegate_requires_explicit_allowlist(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(
            plugin,
            user_blacklist=["20002"],
            cross_sender_download_access_mode="blacklist",
        )
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        owner_key = (plugin._session_id(message), "10001")
        plugin._pending_downloads[owner_key] = [self.pending("https://v.douyin.com/video-a", "视频 A")]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 0
        assert error == "引用的详情卡片不属于你的待下载内容，无法下载。"

    def test_delegate_must_quote_bot_overview(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, cross_sender_download_user_whitelist=["20002"])
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="20002")
        message["raw_message"][0]["data"]["target_message_sender_id"] = "30003"
        owner_key = (plugin._session_id(message), "10001")
        plugin._pending_downloads[owner_key] = [self.pending("https://v.douyin.com/video-a", "视频 A")]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 0
        assert error == "引用的详情卡片不属于你的待下载内容，无法下载。"

    def test_global_sender_blacklist_blocks_own_pending_download(self) -> None:
        plugin = make_plugin()
        enable_advanced_parser(plugin, user_blacklist=["10001"])
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="10001")
        key = (plugin._session_id(message), "10001")
        plugin._pending_downloads[key] = [self.pending("https://v.douyin.com/video-a", "视频 A")]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 0 and error is None

    def test_sent_item_cannot_be_downloaded_twice(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="10001")
        key = (plugin._session_id(message), "10001")
        sent = self.pending("https://v.douyin.com/video-a", "视频 A")
        sent.delivery_state = "sent"
        plugin._pending_downloads[key] = [sent]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 0
        assert error == "该详情卡片的媒体已成功发送，无需重复下载。"

    def test_downloading_item_cannot_be_downloaded_twice(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_message("https://v.douyin.com/video-a", user_id="10001")
        key = (plugin._session_id(message), "10001")
        downloading = self.pending("https://v.douyin.com/video-a", "视频 A")
        downloading.delivery_state = "downloading"
        plugin._pending_downloads[key] = [downloading]

        pending, choices, queue_size, error = plugin._consume_pending_download(
            message, plugin._message_source(message)
        )

        assert pending is None and not choices and queue_size == 0
        assert error == "该详情卡片的媒体正在下载，请勿重复请求。"

    def test_reply_with_auto_at_strips_mention_and_consumes(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_with_at_message("https://v.douyin.com/video-a", user_id="10001")
        key = (plugin._session_id(message), "10001")
        item = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[key] = [item]

        own_source = plugin._message_source(message, include_reply=False)
        assert "[reply]" not in own_source
        assert "@bot群名片" not in own_source
        assert own_source == "下载"
        assert plugin._has_download_command(own_source)

        pending, choices, queue_size, error = plugin._consume_pending_download(message, own_source)
        assert pending is item
        assert not choices and queue_size == 1 and error is None

    def test_reply_with_literal_at_text_still_detects_download(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_at_as_literal_text_message("https://v.douyin.com/video-a", user_id="10001")
        key = (plugin._session_id(message), "10001")
        item = self.pending("https://v.douyin.com/video-a", "视频 A")
        plugin._pending_downloads[key] = [item]

        own_source = plugin._message_source(message, include_reply=False)
        assert own_source == "下载"
        assert plugin._has_download_command(own_source)

        pending, choices, queue_size, error = plugin._consume_pending_download(message, own_source)
        assert pending is item
        assert not choices and queue_size == 1 and error is None


class TestBotAddressed:
    def test_at_bot(self) -> None:
        plugin = make_plugin()
        assert plugin._is_bot_addressed(data.at_bot_message())

    def test_reply_bot(self) -> None:
        plugin = make_plugin()
        assert plugin._is_bot_addressed(data.reply_bot_message())

    def test_reply_other(self) -> None:
        plugin = make_plugin()
        assert not plugin._is_bot_addressed(data.reply_other_message())

    def test_plain_chat(self) -> None:
        plugin = make_plugin()
        assert not plugin._is_bot_addressed(data.plain_chat_message())

    def test_reply_auto_at_marks_bot_addressed_via_is_mentioned(self) -> None:
        plugin = make_plugin()
        message = data.reply_download_with_at_message("https://v.douyin.com/video-a")
        assert message.get("is_mentioned") is True
        assert plugin._is_bot_addressed(message)

    def test_plain_chat_not_mentioned_not_bot_addressed(self) -> None:
        plugin = make_plugin()
        message = data.with_text(data.with_group(data.base_message()), "下载")
        assert message.get("is_mentioned") is not True
        assert not plugin._is_bot_addressed(message)


class TestReplyParsingGuard:
    def test_reply_to_other_card_is_not_a_new_link_share(self) -> None:
        plugin = make_plugin()
        message = data.reply_other_link_message()
        assert plugin._has_reply(message)
        assert "https://v.douyin.com/other-video/" in plugin._message_source(message)
        assert not plugin._has_download_command(plugin._message_source(message, include_reply=False))

    def test_reply_to_other_card_with_download_word_does_not_trigger(self) -> None:
        plugin = make_plugin()
        message = data.reply_other_link_message("这个视频怎么下载？")
        assert plugin._has_reply(message)
        assert not plugin._has_download_command(plugin._message_source(message, include_reply=False))

    def test_reply_to_bot_with_normal_download_sentence_does_not_trigger(self) -> None:
        plugin = make_plugin()
        message = data.reply_bot_message()
        assert plugin._is_bot_addressed(message)
        assert not plugin._has_download_command(plugin._message_source(message, include_reply=False))
