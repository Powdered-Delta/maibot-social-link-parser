# -*- coding: utf-8 -*-
"""入口检查测试数据。

覆盖黑/白名单（群、用户、私聊）、下载确认意图、URL 提取、B站小程序卡片 URL 恢复、
以及是否指向 bot 的判定。消息结构对齐 MaiBot-Napcat-Adapter 的 message dict 形态。
"""

from __future__ import annotations

from typing import Any


def base_message(*, session_id: str = "s1") -> dict[str, Any]:
    return {
        "message_id": "msg-1",
        "timestamp": "1750000000.0",
        "platform": "qq",
        "session_id": session_id,
        "is_at": False,
        "is_mentioned": False,
        "is_emoji": False,
        "is_picture": False,
        "is_command": False,
        "is_notify": False,
        "raw_message": [],
        "processed_plain_text": "",
        "display_message": "",
        "message_info": {
            "user_info": {
                "user_id": "10001",
                "user_nickname": "alice",
                "user_cardname": None,
            },
            "additional_config": {"self_id": "12345", "napcat_message_type": "group"},
        },
    }


def with_group(message: dict[str, Any], group_id: str = "999001") -> dict[str, Any]:
    message["message_info"]["group_info"] = {"group_id": group_id, "group_name": f"group-{group_id}"}
    return message


def with_user(message: dict[str, Any], user_id: str = "10001") -> dict[str, Any]:
    message["message_info"]["user_info"]["user_id"] = user_id
    return message


def with_text(message: dict[str, Any], text: str) -> dict[str, Any]:
    message["processed_plain_text"] = text
    message["raw_message"] = [{"type": "text", "data": text}]
    return message


def with_card(message: dict[str, Any], app: str, meta: dict[str, Any]) -> dict[str, Any]:
    """附加平台卡片负载，模拟 NapCat 适配器保留的原始 JSON 卡片。"""
    payload: dict[str, Any] = {"app": app, "meta": meta}
    card = {"type": "miniapp_card", "app": app, "payload": payload}
    message["message_info"]["additional_config"]["platform_card_payloads"] = [card]
    return message


def bilibili_video_link_message() -> dict[str, Any]:
    """群聊中直接发送的 B站视频链接。"""
    return with_text(
        with_group(base_message()),
        "看看这个 https://www.bilibili.com/video/BV1xx411c7mD/?p=2",
    )


def bilibili_short_link_message() -> dict[str, Any]:
    """群聊中直接发送的 b23.tv 短链。"""
    return with_text(with_group(base_message()), "分享一下 https://b23.tv/abc123")


def bilibili_miniapp_card_message() -> dict[str, Any]:
    """群聊中的 B站小程序卡片。

    NapCat 适配器只把占位文本和预览图留在 raw_message，真实内容链接
    qqdocurl 移入 platform_card_payloads，需由插件补回。
    """
    message = with_group(base_message())
    message["processed_plain_text"] = (
        "[小程序] 哔哩哔哩：[有的没的]我很少用人声鼎沸形容一个队伍 [图片]"
    )
    message["raw_message"] = [
        {"type": "text", "data": "[小程序] 哔哩哔哩：[有的没的]我很少用人声鼎沸形容一个队伍"},
        {"type": "image", "data": "", "hash": "abc"},
    ]
    return with_card(
        message,
        "com.tencent.miniapp_01",
        {
            "detail_1": {
                "title": "【有的没的】我很少用人声鼎沸形容一个队伍",
                "qqdocurl": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "preview": "https://i0.hdslb.com/bfs/archive/cover.jpg",
            }
        },
    )


def bilibili_miniapp_card_shortlink_message() -> dict[str, Any]:
    """群聊中的 B站小程序卡片（qqdocurl 为 b23.tv 短链）+ 下载意图。

    对应真实反馈：卡片标题「【我给油车加入了电车科幻声浪，啊哇哇哇-哔哩哔哩】」，
    内容链接 https://b23.tv/pGGBsrB 只在 platform_card_payloads 里，raw_message
    只有占位文本；末尾另附「下载」意图文本。
    """
    message = with_group(base_message())
    message["processed_plain_text"] = "[小程序] 哔哩哔哩：[标题]\n下载"
    message["raw_message"] = [
        {"type": "text", "data": "[小程序] 哔哩哔哩：[标题]"},
        {"type": "text", "data": "下载"},
    ]
    return with_card(
        message,
        "com.tencent.miniapp_01",
        {
            "detail_1": {
                "title": "【我给油车加入了电车科幻声浪，啊哇哇哇-哔哩哔哩】",
                "qqdocurl": "https://b23.tv/pGGBsrB",
            }
        },
    )


def douyin_short_link_message() -> dict[str, Any]:
    return with_text(with_group(base_message()), "抖音 https://v.douyin.com/xxxxx/")


def xhs_link_message() -> dict[str, Any]:
    return with_text(with_group(base_message()), "小红书 https://www.xiaohongshu.com/explore/123456")


def private_chat_message() -> dict[str, Any]:
    message = base_message()
    message["message_info"].pop("group_info", None)
    message["message_info"]["additional_config"]["napcat_message_type"] = "private"
    return with_text(message, "https://www.bilibili.com/video/BV1xx411c7mD/")


def plain_chat_message() -> dict[str, Any]:
    return with_text(with_group(base_message()), "今晚吃什么？")


def plain_chat_private_message() -> dict[str, Any]:
    message = base_message()
    message["message_info"].pop("group_info", None)
    return with_text(message, "在吗？")


def download_confirm_message(text: str = "下载") -> dict[str, Any]:
    return with_text(with_group(base_message()), text)


def reply_download_message(target_url: str, *, user_id: str = "10001") -> dict[str, Any]:
    """回复 bot 概览消息并发送下载指令。"""
    message = with_user(with_group(base_message()), user_id)
    message["raw_message"] = [
        {
            "type": "reply",
            "data": {
                "target_message_id": "bot-overview",
                "target_message_content": f"标题\n链接：{target_url}\n回复“下载”获取原媒体。",
                "target_message_sender_id": "12345",
            },
        },
        {"type": "text", "data": "下载"},
    ]
    message["processed_plain_text"] = "下载"
    return message


def reply_download_with_at_message(target_url: str, *, user_id: str = "10001") -> dict[str, Any]:
    """引用回复 bot 概览，客户端自动附带 @bot 群名片 的消息。

    对齐 MaiBot-Napcat-Adapter：reply 段渲染为 ``[reply]``，at 段渲染为
    ``@群名片``，与用户文本拼接进 ``processed_plain_text``；@到 bot 时
    ``is_mentioned`` 置位。
    """
    message = with_user(with_group(base_message()), user_id)
    message["is_mentioned"] = True
    message["is_at"] = True
    message["raw_message"] = [
        {
            "type": "reply",
            "data": {
                "target_message_id": "bot-overview",
                "target_message_content": f"标题\n链接：{target_url}\n回复“下载”获取原媒体。",
                "target_message_sender_id": "12345",
            },
        },
        {
            "type": "at",
            "data": {"target_user_id": "12345", "target_user_cardname": "bot群名片"},
        },
        {"type": "text", "data": "下载"},
    ]
    message["processed_plain_text"] = "[reply]@bot群名片下载"
    return message


def at_bot_message() -> dict[str, Any]:
    message = with_group(base_message())
    message["is_at"] = True
    return with_text(message, "帮我看看这个视频怎么下载")


def reply_bot_message() -> dict[str, Any]:
    """回复了 bot 的私聊/群聊消息。"""
    message = with_group(base_message())
    message["raw_message"] = [
        {
            "type": "reply",
            "data": {
                "target_message_id": "r1",
                "target_message_content": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "target_message_sender_id": "12345",
            },
        },
        {"type": "text", "data": "这个视频帮我下载"},
    ]
    message["processed_plain_text"] = "这个视频帮我下载"
    return message


def reply_other_message() -> dict[str, Any]:
    """回复了别的用户（sender 不是 bot）。"""
    message = with_group(base_message())
    message["raw_message"] = [
        {
            "type": "reply",
            "data": {
                "target_message_id": "r2",
                "target_message_content": "晚上好",
                "target_message_sender_id": "20002",
            },
        },
        {"type": "text", "data": "晚上好呀"},
    ]
    message["processed_plain_text"] = "晚上好呀"
    return message


def reply_other_link_message(text: str = "这个视频怎么下载？") -> dict[str, Any]:
    """回复其他人的社媒卡片，引用内容含可识别 URL。"""
    message = with_group(base_message())
    message["raw_message"] = [
        {
            "type": "reply",
            "data": {
                "target_message_id": "r-link",
                "target_message_content": "抖音视频 https://v.douyin.com/other-video/",
                "target_message_sender_id": "20002",
            },
        },
        {"type": "text", "data": text},
    ]
    message["processed_plain_text"] = text
    return message


# ---------------------------------------------------------------
# 白/黑名单场景（高级分组开启时生效）
# ---------------------------------------------------------------

GROUP_BLOCKED = with_group(base_message(session_id="s-group-blocked"))
GROUP_ALLOWED = with_group(base_message(session_id="s-group-allowed"), group_id="999002")
GROUP_OTHER = with_group(base_message(session_id="s-group-other"), group_id="999999")

USER_BLOCKED = with_user(with_group(base_message(session_id="s-user-blocked")), "666001")
USER_ALLOWED = with_user(with_group(base_message(session_id="s-user-allowed")), "666002")
USER_OTHER = with_user(with_group(base_message(session_id="s-user-other")), "666003")

PRIVATE_BLOCKED = with_user(
    with_text(base_message(session_id="s-private-blocked"), "https://v.douyin.com/xxxxx/"),
    "777001",
)
PRIVATE_ALLOWED = with_user(
    with_text(base_message(session_id="s-private-allowed"), "https://v.douyin.com/xxxxx/"),
    "777002",
)
PRIVATE_OTHER = with_user(
    with_text(base_message(session_id="s-private-other"), "https://v.douyin.com/xxxxx/"),
    "777003",
)
