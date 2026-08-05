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
from typing import Any, ClassVar, Literal

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode, HookOrder

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from .core.config import PluginConfig as CorePluginConfig
from .core.data import DynamicContent, GraphicsContent, ImageContent, ParseResult, TextContent, VideoContent
from .core.download import Downloader, reset_download_progress_callback, set_download_progress_callback
from .core.exception import ParseException
from .core.parsers import BaseParser
from .sender import (
    ApiSettings,
    image_segment,
    send_emoji_reaction,
    send_group_forward,
    send_image,
    send_overview_hybrid,
    send_text,
    send_video,
    text_segment,
)

URL_RE = re.compile(r"https?://[^\s\]\)）>\"']+")


def _ui(
    label: str,
    hint: str,
    order: int,
    *,
    label_en: str,
    hint_en: str,
    label_ja: str,
    hint_ja: str,
    **extra: Any,
) -> dict[str, Any]:
    """构造统一的 WebUI 字段多语言元数据。"""
    return {
        "label": label,
        "hint": hint,
        "order": order,
        "i18n": {
            "en_US": {"label": label_en, "hint": hint_en},
            "ja_JP": {"label": label_ja, "hint": hint_ja},
        },
        **extra,
    }


class PluginSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "插件设置"
    __ui_icon__: ClassVar[str] = "settings"
    __ui_order__: ClassVar[int] = 0

    name: str = Field(default="maibot_social_link_parser", description="插件内部名称", json_schema_extra=_ui("插件内部名称", "仅用于标识配置；通常无需修改。", 0, label_en="Internal name", hint_en="Used only to identify this configuration. Usually leave unchanged.", label_ja="内部名", hint_ja="設定識別用です。通常は変更不要です。", disabled=True))
    config_version: str = Field(default="0.3.4", description="配置文件版本", json_schema_extra=_ui("配置版本", "由插件用于补齐配置字段，请勿手动修改。", 1, label_en="Configuration version", hint_en="Used to merge configuration fields. Do not edit manually.", label_ja="設定バージョン", hint_ja="設定項目の統合に使用します。手動で変更しないでください。", disabled=True))
    version: str = Field(default="0.3.4", description="插件版本", json_schema_extra=_ui("插件版本", "当前插件版本，仅供查看。", 2, label_en="Plugin version", hint_en="Current plugin version. Read only.", label_ja="プラグインバージョン", hint_ja="現在のプラグインバージョンです。読み取り専用です。", disabled=True))
    enabled: bool = Field(default=True, description="是否启用插件", json_schema_extra=_ui("启用插件", "关闭后不再识别或处理社媒链接。", 3, label_en="Enable plugin", hint_en="When disabled, social media links are not detected or handled.", label_ja="プラグインを有効化", hint_ja="無効時はソーシャルメディアのリンクを処理しません。"))
    dry_run: bool = Field(default=False, description="演练模式", json_schema_extra=_ui("演练模式", "不向 QQ/NapCat 发送任何消息，仅记录拟发送内容；仍会下载媒体，可能消耗流量和磁盘。", 4, label_en="Dry run", hint_en="Prevents all QQ/NapCat sends and logs intended output. Downloads still run and use bandwidth and disk.", label_ja="ドライラン", hint_ja="QQ/NapCat への送信を行わず、送信予定をログに出します。ダウンロードは実行されます。"))


class ParserSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "解析与下载"
    __ui_icon__: ClassVar[str] = "link"
    __ui_order__: ClassVar[int] = 1

    enabled_platforms: list[Literal["douyin", "xhs", "xiaoheihe", "bilibili"]] = Field(
        default=["douyin", "xhs", "xiaoheihe"],
        description="多选启用的平台。B站默认关闭以避免与独立 B站插件重复回复。",
        json_schema_extra={
            **_ui("启用平台", "多选需要自动解析的平台。B站默认关闭，避免与独立 B站插件重复回复。", 0, label_en="Enabled platforms", hint_en="Select platforms to parse automatically. Bilibili is off by default to avoid duplicate replies from a dedicated plugin.", label_ja="有効なプラットフォーム", hint_ja="自動解析するプラットフォームを選択します。Bilibili は重複返信を避けるため既定で無効です。"),
            "widget": "checkboxes",
            "options": [
                {"label": "抖音", "value": "douyin"},
                {"label": "小红书", "value": "xhs"},
                {"label": "小黑盒", "value": "xiaoheihe"},
                {"label": "B站（默认关闭）", "value": "bilibili"},
            ],
        },
    )
    block_ai_reply: bool = Field(default=True, description="阻止普通 AI 回复", json_schema_extra=_ui("阻止普通 AI 回复", "链接命中后中止 MaiBot 常规聊天处理，避免链接解析和 AI 回复重复发送。", 1, label_en="Block normal AI reply", hint_en="Stops regular MaiBot chat handling after a matching link to avoid duplicate parser and AI replies.", label_ja="通常の AI 返信を停止", hint_ja="リンク検出後に通常のチャット処理を止め、解析結果との重複返信を避けます。"))
    download_mode: Literal["confirm", "automatic"] = Field(default="confirm", description="下载模式", json_schema_extra={**_ui("下载模式", "确认下载更适合生产群聊：先发概览，只有原发送者回复“下载”才下载完整媒体。自动下载会立即消耗带宽和存储。", 2, label_en="Download mode", hint_en="Confirm is safer for groups: send an overview first and download only after the original sender replies. Automatic download immediately uses bandwidth and storage.", label_ja="ダウンロードモード", hint_ja="確認モードはグループ向けです。概要を送信後、元の送信者の確認でダウンロードします。自動モードは即座に通信量とストレージを消費します。"), "widget": "select", "options": [{"label": "确认后下载", "value": "confirm"}, {"label": "自动下载", "value": "automatic"}]})
    send_preview_image: bool = Field(default=True, description="是否发送封面或首图预览", json_schema_extra=_ui("发送封面或首图预览", "确认模式下下载视频封面或图集首图，用于让用户在完整下载前确认内容。", 3, label_en="Send cover or first-image preview", hint_en="In confirm mode, download a video cover or first gallery image so users can verify content before the full download.", label_ja="カバーまたは先頭画像のプレビューを送信", hint_ja="確認モードでは、完全なダウンロード前に内容を確認できるよう動画カバーまたは先頭画像を取得します。"))


class ParserAdvancedSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "解析与下载 · 高级"
    __ui_icon__: ClassVar[str] = "tune"
    __ui_order__: ClassVar[int] = 2

    enabled: bool = Field(default=False, description="启用高级解析配置", json_schema_extra=_ui("启用高级解析配置", "关闭时忽略本分组全部字段并使用内置默认值；需要精细控制黑白名单、去重、媒体限制或表情回应时再开启。", 0, label_en="Enable advanced parser settings", hint_en="When disabled, all fields in this group are ignored and built-in defaults are used. Turn on only when you need fine control over allow/deny lists, deduplication, media limits, or reactions.", label_ja="詳細な解析設定を有効化", hint_ja="無効時は本グループの全項目を無視し、内蔵デフォルト値を使用します。許可・拒否リスト、重複除外、メディア制限、リアクションの細かい調整が必要な場合のみ有効化してください。"))
    group_whitelist: list[str] = Field(default_factory=list, description="自动解析群白名单", json_schema_extra=_ui("自动解析群白名单", "仅这些 QQ 群自动解析；留空表示所有群。填入群号字符串。", 1, label_en="Group allowlist", hint_en="Only parse automatically in these QQ groups. Leave empty for all groups. Enter group IDs as strings.", label_ja="グループ許可リスト", hint_ja="指定した QQ グループでのみ自動解析します。空欄は全グループです。グループ ID を文字列で入力します。"))
    group_blacklist: list[str] = Field(default_factory=list, description="自动解析群黑名单", json_schema_extra=_ui("自动解析群黑名单", "这些 QQ 群不自动解析。填入群号字符串。与白名单同时命中时黑名单优先。", 1, label_en="Group denylist", hint_en="Never parse automatically in these QQ groups. Enter group IDs as strings. Denylist wins when it overlaps the allowlist.", label_ja="グループ拒否リスト", hint_ja="自動解析しない QQ グループです。グループ ID を文字列で入力します。許可リストと重複した場合は拒否が優先されます。"))
    user_whitelist: list[str] = Field(default_factory=list, description="链接发送者全局白名单", json_schema_extra=_ui("链接发送者全局白名单", "仅这些 QQ 用户发送的链接会解析、创建待下载任务或直接下载；留空表示所有用户。代触发者也默认遵守此名单，但被显式加入代触发白名单的用户或群可例外。", 1, label_en="Global link-sender allowlist", hint_en="Only links sent by these QQ users are parsed, queued, or directly downloaded. Leave empty for all users. Delegated requesters also follow this list unless their user or group is explicitly allowlisted for delegation.", label_ja="リンク送信者のグローバル許可リスト", hint_ja="指定した QQ ユーザーが送信したリンクのみ解析、待機登録、直接ダウンロードします。空欄は全ユーザーです。代理実行者も原則このリストに従いますが、代理許可リストに明示登録されたユーザーまたはグループは例外です。"))
    user_blacklist: list[str] = Field(default_factory=list, description="链接发送者全局黑名单", json_schema_extra=_ui("链接发送者全局黑名单", "这些 QQ 用户发送的链接不解析、不会创建待下载任务或直接下载。黑名单优先于白名单；代触发者也默认受限，但显式代触发白名单可例外。", 1, label_en="Global link-sender denylist", hint_en="Links from these QQ users are not parsed, queued, or directly downloaded. Denylist wins over allowlist. Delegated requesters are also blocked by default, except when explicitly allowlisted for delegation.", label_ja="リンク送信者のグローバル拒否リスト", hint_ja="指定した QQ ユーザーのリンクは解析、待機登録、直接ダウンロードしません。拒否リストは許可リストより優先されます。代理実行者も原則制限されますが、代理許可リストに明示登録された場合は例外です。"))
    private_whitelist: list[str] = Field(default_factory=list, description="私聊自动解析用户白名单", json_schema_extra=_ui("私聊自动解析用户白名单", "仅这些 QQ 用户的私聊链接会解析；留空表示允许所有私聊。填入 QQ 号字符串。", 1, label_en="Private-chat user allowlist", hint_en="Only parse private-chat links from these QQ users. Leave empty to allow all private chats. Enter QQ numbers as strings.", label_ja="個別チャット許可リスト", hint_ja="指定した QQ ユーザーとの個別チャットのリンクのみ解析します。空欄はすべての個別チャットを許可します。QQ 番号を文字列で入力します。"))
    private_blacklist: list[str] = Field(default_factory=list, description="私聊自动解析用户黑名单", json_schema_extra=_ui("私聊自动解析用户黑名单", "这些 QQ 用户的私聊链接不解析。填入 QQ 号字符串。与白名单同时命中时黑名单优先。", 1, label_en="Private-chat user denylist", hint_en="Never parse private-chat links from these QQ users. Enter QQ numbers as strings. Denylist wins when it overlaps the allowlist.", label_ja="個別チャット拒否リスト", hint_ja="指定した QQ ユーザーとの個別チャットのリンクを解析しません。QQ 番号を文字列で入力します。許可リストと重複した場合は拒否が優先されます。"))
    download_intent_regex: str = Field(default="", description="自定义下载确认意图正则", json_schema_extra=_ui("自定义下载确认意图正则", "追加的独立下载确认命令正则，与默认的“下载”取并集；同时支持“下载 1”这类编号选择。留空仅使用默认命令。", 1, label_en="Custom download-command regex", hint_en="Extra standalone download-command regex combined with the default 下载 command. Numbered selections such as 下载 1 are supported. Leave empty to use the default only.", label_ja="ダウンロード確認コマンドのカスタム正規表現", hint_ja="既定の「下载」コマンドと併用する独立した確認ダウンロード用の正規表現です。「下载 1」のような番号指定も対応します。空欄は既定のみです。", placeholder=r"下載|down ?load"))
    debounce_seconds: int = Field(default=120, description="链接去重时间", ge=0, json_schema_extra=_ui("链接去重时间（秒）", "同一会话内重复发送同一链接时的忽略时长；设为 0 禁用去重。", 2, label_en="Link deduplication (seconds)", hint_en="Ignore repeated copies of the same link in one session. Set 0 to disable.", label_ja="リンク重複除外（秒）", hint_ja="同一セッション内で同じリンクを再送した場合の無視時間です。0 で無効化します。"))
    max_images: int = Field(default=9, description="单条链接最多发送图片数", ge=1, le=30, json_schema_extra=_ui("最多发送图片数", "完整图集发送时的最大图片数；超过部分不会发送。", 3, label_en="Maximum images", hint_en="Maximum images sent for one link. Extra images are skipped.", label_ja="最大画像数", hint_ja="1 リンクで送信する画像数の上限です。超過分は送信しません。"))
    max_text_chars: int = Field(default=700, description="摘要正文最大字符数", ge=80, le=3000, json_schema_extra=_ui("摘要最大字符数", "过长的标题、正文或摘要会被截断。", 4, label_en="Maximum summary characters", hint_en="Long titles, body text, and summaries are truncated.", label_ja="概要の最大文字数", hint_ja="長いタイトル、本文、概要は切り詰められます。"))
    send_images: bool = Field(default=True, description="是否发送图片", json_schema_extra=_ui("发送图片", "关闭后图集和图片媒体不会发送；视频封面预览仍由“发送预览图”控制。", 5, label_en="Send images", hint_en="When disabled, galleries and image media are not sent. Video preview is controlled separately.", label_ja="画像を送信", hint_ja="無効時は画像とギャラリーを送信しません。動画プレビューは別設定です。"))
    send_video: bool = Field(default=True, description="是否发送视频", json_schema_extra=_ui("发送视频", "关闭后仍可解析并发送概览，但确认下载后不会回传视频。", 6, label_en="Send videos", hint_en="When disabled, videos are parsed and summarized but are not sent after confirmation.", label_ja="動画を送信", hint_ja="無効時も解析と概要送信は行いますが、確認後に動画は送信しません。"))
    use_forward_for_multi: bool = Field(default=True, description="多图使用合并转发", json_schema_extra=_ui("多图使用合并转发", "静态图集和图文使用合并转发；关闭后图片和失败占位符将逐条发送。视频或 Live Photo 的混合图集仍逐条发送。", 7, label_en="Use merged forwarding for galleries", hint_en="Uses merged forwarding for static galleries and posts. When disabled, images and failure placeholders are sent one by one. Mixed galleries with video or Live Photo are still sent one by one.", label_ja="ギャラリーを結合転送で送信", hint_ja="静的ギャラリーと投稿に結合転送を使用します。無効時は画像と失敗表示を個別に送信します。動画や Live Photo を含む混合ギャラリーは個別送信のままです。"))
    gallery_delivery_mode: Literal["default", "http_forward"] = Field(default="default", description="图集发送通道", json_schema_extra={**_ui("图集发送通道", "默认跟随 QQ 发送通道；WebSocket 会使用 MaiBot 的合并转发能力。选择“HTTP 合并转发”时，静态图集和图文会通过 OneBot HTTP 发送；需要在“HTTP 兼容后备”中填写可访问的 NapCat HTTP 地址。发送失败时回退为默认通道逐条发送。含视频或 Live Photo 的混合图集仍使用默认通道逐条发送。", 8, label_en="Gallery delivery channel", hint_en="Default follows the QQ delivery channel; WebSocket uses MaiBot's merged-forward capability. HTTP forward sends static galleries through OneBot HTTP and requires the HTTP fallback settings. Failed delivery falls back to the default channel one item at a time. Mixed galleries with video or Live Photo still use the default channel.", label_ja="ギャラリー配信チャネル", hint_ja="既定では QQ 配信チャネルに従い、WebSocket は MaiBot の結合転送機能を使用します。HTTP 結合転送では静的ギャラリーを OneBot HTTP で送信し、HTTP 互換設定が必要です。失敗時は既定チャネルで個別送信します。動画や Live Photo を含む混合ギャラリーは既定チャネルを使用します。"), "widget": "select", "options": [{"label": "跟随默认通道", "value": "default"}, {"label": "HTTP 合并转发", "value": "http_forward"}]})
    pending_download_ttl_seconds: int = Field(default=600, description="等待确认有效期", ge=60, le=3600, json_schema_extra=_ui("等待确认有效期（秒）", "确认下载任务过期后需要重新发送链接。", 9, label_en="Confirmation expiry (seconds)", hint_en="After expiration, send the link again to create a new download request.", label_ja="確認の有効期限（秒）", hint_ja="期限切れ後は、新しいダウンロード要求を作るためにリンクを再送してください。"))
    pending_download_selection: Literal["latest", "number"] = Field(default="latest", description="多条待下载内容的选择方式", json_schema_extra={**_ui("多条待下载内容的选择方式", "回复 bot 的详情卡片并发送“下载”时，会按引用卡片中的链接精确选择内容；直接发送“下载”时，默认选择同一会话内最新一条。选择“回复编号”后，存在多条内容时必须发送“下载 1”“下载 2”等明确编号。", 10, label_en="Multiple pending-download selection", hint_en="Replying Download to a bot overview selects its referenced link. A plain Download selects the latest item in the same session. Choose Number to require Download 1, Download 2, etc. when multiple items are pending.", label_ja="複数待機ダウンロードの選択方法", hint_ja="Bot の詳細カードに「ダウンロード」で返信した場合、引用リンクに対応する内容を選択します。通常の「ダウンロード」は同じセッションの最新項目を選びます。番号指定では、複数ある場合に「ダウンロード 1」などを要求します。"), "widget": "select", "options": [{"label": "最新一条（默认）", "value": "latest"}, {"label": "回复编号", "value": "number"}]})
    cross_sender_download_group_whitelist: list[str] = Field(default_factory=list, description="跨用户下载群白名单", json_schema_extra=_ui("跨用户下载群白名单", "这些群内，任何用户回复 bot 的详情卡片都可下载原发送者的待下载媒体。留空表示不按群放宽权限；填入群号字符串。", 11, label_en="Cross-user download group allowlist", hint_en="In these groups, anyone replying to a bot overview may download the original sender's pending media. Leave empty to keep group permissions strict. Enter group IDs as strings.", label_ja="ユーザー横断ダウンロードのグループ許可リスト", hint_ja="指定グループでは、誰でも Bot の詳細カードに返信して元送信者の待機メディアをダウンロードできます。空欄ではグループによる緩和を行いません。グループ ID を文字列で入力します。"))
    cross_sender_download_user_whitelist: list[str] = Field(default_factory=list, description="跨用户下载用户白名单", json_schema_extra=_ui("跨用户下载用户白名单", "这些用户回复 bot 的详情卡片时可下载其他发送者的待下载媒体。留空表示不按用户放宽权限；填入 QQ 号字符串。", 11, label_en="Cross-user download user allowlist", hint_en="These users may download another sender's pending media by replying to a bot overview. Leave empty to keep user permissions strict. Enter QQ numbers as strings.", label_ja="ユーザー横断ダウンロードのユーザー許可リスト", hint_ja="指定ユーザーは Bot の詳細カードへの返信で、他送信者の待機メディアをダウンロードできます。空欄ではユーザーによる緩和を行いません。QQ 番号を文字列で入力します。"))
    reaction_emoji_enabled: bool = Field(default=True, description="识别后表情回应", json_schema_extra=_ui("识别后表情回应", "对群聊原消息调用 NapCat 表情回应 API；私聊不会执行。", 12, label_en="React after recognition", hint_en="Calls NapCat's reaction API for the original group message. It is not used in private chats.", label_ja="認識後にリアクション", hint_ja="元のグループメッセージに NapCat のリアクション API を使用します。個別チャットでは実行しません。"))
    reaction_emoji_id: int = Field(default=76, description="QQ 表情回应 ID", ge=1, json_schema_extra=_ui("QQ 表情回应 ID", "NapCat `set_msg_emoji_like` 使用的数字表情 ID。仅在已启用表情回应时生效。", 13, label_en="QQ reaction emoji ID", hint_en="Numeric emoji ID for NapCat set_msg_emoji_like. Used only when reactions are enabled.", label_ja="QQ リアクション絵文字 ID", hint_ja="NapCat set_msg_emoji_like に使用する数値 ID です。リアクション有効時のみ使用します。"))
    source_max_size_mb: int = Field(default=80, description="单媒体最大下载大小", ge=1, le=300, json_schema_extra=_ui("单媒体最大下载大小（MB）", "单个图片、视频或音频超过此值会被拒绝下载。提高该值会增加磁盘、网络和 QQ 发送失败风险。", 14, label_en="Maximum media size (MB)", hint_en="Images, videos, or audio above this size are not downloaded. Increasing it raises disk, bandwidth, and QQ delivery failure risk.", label_ja="メディア最大サイズ（MB）", hint_ja="このサイズを超える画像、動画、音声はダウンロードしません。増やすとディスク、通信量、QQ 送信失敗のリスクが上がります。"))
    source_max_minutes: int = Field(default=8, description="视频最大时长", ge=1, le=60, json_schema_extra=_ui("视频最大时长（分钟）", "超过此时长的视频不下载，避免意外的大文件任务。", 15, label_en="Maximum video duration (minutes)", hint_en="Longer videos are not downloaded to avoid unexpected large jobs.", label_ja="動画の最大時間（分）", hint_ja="予期しない大きなタスクを避けるため、この時間を超える動画はダウンロードしません。"))


    cross_sender_download_access_mode: Literal["disabled", "whitelist", "blacklist"] = Field(
        default="whitelist",
        description="跨用户下载授权模式",
        json_schema_extra={
            **_ui(
                "跨用户下载授权模式",
                "禁用：仅原发送者；白名单：群或用户命中白名单才允许；黑名单：除命中黑名单的群和用户外均允许跨用户下载。",
                16,
                label_en="Cross-user download access mode",
                hint_en="Disabled: original sender only. Allowlist: listed groups or users only. Denylist: everyone except blocked groups and users.",
                label_ja="ユーザー横断ダウンロード認可モード",
                hint_ja="無効は元送信者のみ、許可リストは指定グループまたはユーザーのみ、拒否リストはブロック対象以外を許可します。",
            ),
            "widget": "select",
            "options": [
                {"label": "白名单", "value": "whitelist"},
                {"label": "黑名单", "value": "blacklist"},
                {"label": "禁用", "value": "disabled"},
            ],
        },
    )
    cross_sender_download_group_blacklist: list[str] = Field(default_factory=list)
    cross_sender_download_user_blacklist: list[str] = Field(default_factory=list)


class NetworkSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "网络与下载"
    __ui_icon__: ClassVar[str] = "network_check"
    __ui_order__: ClassVar[int] = 3

    proxy: str = Field(default="", description="解析/下载代理", json_schema_extra=_ui("解析/下载代理", "可选。填写 MaiBot 容器可访问的 HTTP/HTTPS 代理地址，例如 http://proxy:7890。留空直连；不要填写宿主机的 127.0.0.1，除非代理也在 MaiBot 容器内。", 0, label_en="Parser/download proxy", hint_en="Optional HTTP/HTTPS proxy reachable from the MaiBot container, e.g. http://proxy:7890. Leave empty for direct access. Do not use host 127.0.0.1 unless the proxy runs inside the MaiBot container.", label_ja="解析・ダウンロードプロキシ", hint_ja="任意。MaiBot コンテナから到達できる HTTP/HTTPS プロキシを指定します。空欄は直接接続です。プロキシが同一コンテナ内でない限り、ホストの 127.0.0.1 は使用しないでください。", placeholder="http://proxy:7890"))


class NetworkAdvancedSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "网络与下载 · 高级"
    __ui_icon__: ClassVar[str] = "tune"
    __ui_order__: ClassVar[int] = 4

    enabled: bool = Field(default=False, description="启用高级网络配置", json_schema_extra=_ui("启用高级网络配置", "关闭时忽略本分组全部字段并使用内置默认值；仅在需要按平台走代理或调整超时、重试时再开启。", 0, label_en="Enable advanced network settings", hint_en="When disabled, all fields in this group are ignored and built-in defaults are used. Turn on only when you need per-platform proxies or custom timeouts and retries.", label_ja="詳細なネットワーク設定を有効化", hint_ja="無効時は本グループの全項目を無視し、内蔵デフォルト値を使用します。プラットフォーム別のプロキシやタイムアウト・再試行の調整が必要な場合のみ有効化してください。"))
    use_proxy_platforms: list[Literal["douyin", "xhs", "xiaoheihe", "bilibili"]] = Field(default_factory=list, description="使用代理的平台", json_schema_extra={**_ui("使用代理的平台", "仅选中的平台通过上方代理访问。未选择的平台保持直连。", 1, label_en="Platforms using proxy", hint_en="Only selected platforms use the proxy above. Others connect directly.", label_ja="プロキシを使用するプラットフォーム", hint_ja="選択したプラットフォームのみ上記プロキシを使用します。その他は直接接続です。"), "widget": "checkboxes", "options": [{"label": "抖音", "value": "douyin"}, {"label": "小红书", "value": "xhs"}, {"label": "小黑盒", "value": "xiaoheihe"}, {"label": "B站", "value": "bilibili"}]})
    common_timeout: int = Field(default=30, description="解析请求超时", ge=5, le=120, json_schema_extra=_ui("解析请求超时（秒）", "页面解析、短链跳转等普通网络请求的总超时。网络较慢时可适当提高。", 2, label_en="Parser request timeout (seconds)", hint_en="Total timeout for page parsing and short-link redirects. Increase slightly on slow networks.", label_ja="解析リクエストのタイムアウト（秒）", hint_ja="ページ解析と短縮リンクのリダイレクトに使う合計タイムアウトです。低速回線では少し増やせます。"))
    download_timeout: int = Field(default=120, description="媒体下载超时", ge=10, le=600, json_schema_extra=_ui("媒体下载超时（秒）", "单次媒体下载请求的总超时。大视频或低速代理可能需要更高值。", 3, label_en="Media download timeout (seconds)", hint_en="Total timeout for one media download request. Large videos or slow proxies may need a higher value.", label_ja="メディアダウンロードのタイムアウト（秒）", hint_ja="1 回のメディアダウンロードの合計タイムアウトです。大きな動画や低速プロキシでは増やす必要があります。"))
    download_retry_times: int = Field(default=3, description="媒体下载重试次数", ge=0, le=5, json_schema_extra=_ui("媒体下载重试次数", "网络中断、响应不完整等可恢复错误的额外重试次数。重试会增加耗时；抖音失效 CDN 链接可能仍需重新解析。", 4, label_en="Media download retries", hint_en="Extra retries for recoverable errors such as interrupted or incomplete responses. Retries increase latency; expired Douyin CDN links may still require a new parse.", label_ja="メディアダウンロードの再試行回数", hint_ja="中断や不完全な応答など、回復可能なエラーの追加再試行回数です。再試行は時間を要し、期限切れの Douyin CDN リンクは再解析が必要な場合があります。"))


class CookieSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "平台 Cookie"
    __ui_icon__: ClassVar[str] = "cookie"
    __ui_order__: ClassVar[int] = 5

    douyin: str = Field(default="", description="抖音 Cookie", json_schema_extra=_ui("抖音 Cookie", "可选。仅在公开解析受限或下载被拒绝时填写浏览器导出的 Cookie。属于敏感凭据，请勿提交、截图或分享。", 0, label_en="Douyin cookie", hint_en="Optional. Use a browser-exported cookie only when public parsing is limited or downloads are rejected. This is sensitive; do not commit, screenshot, or share it.", label_ja="Douyin Cookie", hint_ja="任意。公開解析やダウンロードが拒否される場合のみ、ブラウザからエクスポートした Cookie を使用します。機密情報のため、コミット、スクリーンショット、共有は禁止です。", input_type="password", multiline=True))
    xhs: str = Field(default="", description="小红书 Cookie", json_schema_extra=_ui("小红书 Cookie", "可选。用于提高公开笔记解析稳定性；应从本人浏览器导出，且不得提交到 Git。", 1, label_en="Xiaohongshu cookie", hint_en="Optional. Improves public-note parsing stability. Export from your own browser and never commit it to Git.", label_ja="Xiaohongshu Cookie", hint_ja="任意。公開ノート解析の安定性を改善します。自身のブラウザからエクスポートし、Git にコミットしないでください。", input_type="password", multiline=True))
    xiaoheihe: str = Field(default="", description="小黑盒 Cookie", json_schema_extra=_ui("小黑盒 Cookie", "可选。只有公开内容无法解析时才需要填写。", 2, label_en="Xiaoheihe cookie", hint_en="Optional. Required only when public content cannot be parsed.", label_ja="Xiaoheihe Cookie", hint_ja="任意。公開コンテンツを解析できない場合のみ必要です。", input_type="password", multiline=True))
    bilibili: str = Field(default="", description="B站 Cookie", json_schema_extra=_ui("B站 Cookie", "可选。启用 B站平台后，可用于访问更高画质或受游客限制的内容。必须使用自己的 Cookie，并注意登录态过期。", 3, label_en="Bilibili cookie", hint_en="Optional. When Bilibili is enabled, it may allow higher quality or guest-restricted content. Use only your own cookie and expect sessions to expire.", label_ja="Bilibili Cookie", hint_ja="任意。Bilibili を有効化した場合、高画質やゲスト制限コンテンツに使用できます。自身の Cookie のみを使い、期限切れに注意してください。", input_type="password", multiline=True))


class DeliveryConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "QQ 发送通道"
    __ui_icon__: ClassVar[str] = "send"
    __ui_order__: ClassVar[int] = 6

    delivery_mode: Literal["websocket", "http"] = Field(default="websocket", description="QQ 消息发送通道", json_schema_extra={**_ui("QQ 消息发送通道", "默认通过 MaiBot 的 NapCat WebSocket 网关发送。仅在网关不可用或需要使用旧版 OneBot HTTP 直连时选择 HTTP。", 0, label_en="QQ delivery channel", hint_en="Uses MaiBot's NapCat WebSocket gateway by default. Choose HTTP only when the gateway is unavailable or legacy direct OneBot HTTP is required.", label_ja="QQ 配信チャネル", hint_ja="既定では MaiBot の NapCat WebSocket ゲートウェイを使用します。ゲートウェイが使えない場合や従来の OneBot HTTP 直結が必要な場合のみ HTTP を選択してください。"), "widget": "select", "options": [{"label": "NapCat WebSocket（推荐）", "value": "websocket"}, {"label": "OneBot HTTP（兼容）", "value": "http"}]})


class ApiConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "HTTP 兼容后备"
    __ui_icon__: ClassVar[str] = "http"
    __ui_order__: ClassVar[int] = 7

    host: str = Field(default="127.0.0.1", description="OneBot HTTP API 主机", json_schema_extra=_ui("OneBot HTTP API 主机", "仅 HTTP 通道使用。容器部署时填写 NapCat 服务名或容器 IP；127.0.0.1 指向 MaiBot 容器自身，通常不是 NapCat。", 1, label_en="OneBot HTTP API host", hint_en="Used only by HTTP delivery. In Docker, use the NapCat service name or container IP; 127.0.0.1 points to the MaiBot container itself and is usually not NapCat.", label_ja="OneBot HTTP API ホスト", hint_ja="HTTP 配信時のみ使用します。Docker では NapCat のサービス名またはコンテナ IP を使用します。127.0.0.1 は MaiBot コンテナ自身を指すため、通常は NapCat ではありません。", placeholder="napcat"))
    port: int = Field(default=3000, description="OneBot HTTP API 端口", ge=1, le=65535, json_schema_extra=_ui("OneBot HTTP API 端口", "仅 HTTP 通道使用。与 NapCat 正向 HTTP 服务监听端口一致，不是 WebSocket 端口。", 2, label_en="OneBot HTTP API port", hint_en="Used only by HTTP delivery. Must match NapCat's forward HTTP listening port, not its WebSocket port.", label_ja="OneBot HTTP API ポート", hint_ja="HTTP 配信時のみ使用します。NapCat の順方向 HTTP 待受ポートと一致させます。WebSocket ポートではありません。"))
    token: str = Field(default="", description="OneBot HTTP API Token", json_schema_extra=_ui("OneBot HTTP API Token", "仅 HTTP 通道使用。NapCat 启用访问令牌时填写相同 Token；留空表示不使用鉴权。属于敏感凭据，请勿提交或写入日志。", 3, label_en="OneBot HTTP API token", hint_en="Used only by HTTP delivery. Enter the same token when NapCat access-token authentication is enabled. This is sensitive; do not commit or log it.", label_ja="OneBot HTTP API トークン", hint_ja="HTTP 配信時のみ使用します。NapCat のアクセストークン認証を有効にした場合は同じトークンを入力します。機密情報のため、コミットやログ出力は禁止です。", input_type="password"))
    bot_uin: str = Field(default="", description="合并转发 Bot QQ", json_schema_extra=_ui("合并转发 Bot QQ", "仅 HTTP 通道使用。群合并转发节点显示的发送者 QQ 号；建议填写当前 NapCat 登录的机器人 QQ。", 4, label_en="Forward-message bot QQ", hint_en="Used only by HTTP delivery. QQ number displayed as the sender of group-forward nodes; use the account logged into NapCat.", label_ja="結合転送の Bot QQ", hint_ja="HTTP 配信時のみ使用します。グループ結合転送ノードの送信者として表示する QQ 番号です。NapCat にログインしている Bot QQ を使用します。", placeholder="123456789"))


class PluginConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    parser: ParserSectionConfig = Field(default_factory=ParserSectionConfig)
    parser_advanced: ParserAdvancedSectionConfig = Field(default_factory=ParserAdvancedSectionConfig)
    network: NetworkSectionConfig = Field(default_factory=NetworkSectionConfig)
    network_advanced: NetworkAdvancedSectionConfig = Field(default_factory=NetworkAdvancedSectionConfig)
    cookies: CookieSectionConfig = Field(default_factory=CookieSectionConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


@dataclass(slots=True)
class PendingDownload:
    """A confirmation-bound download request for one sender in one conversation."""

    parser: BaseParser
    keyword: str
    searched: Any
    url: str
    reference_urls: tuple[str, ...]
    title: str
    expires_at: float
    delivery_state: Literal["pending", "downloading", "sent"] = "pending"


class MultiPlatformParserPlugin(MaiBotPlugin):
    """自动解析抖音、小红书、小黑盒等社媒链接。"""

    config_model = PluginConfig
    config_reload_subscriptions: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self._downloader: Downloader | None = None
        self._parsers: list[BaseParser] = []
        self._recent: dict[tuple[str, str], float] = {}
        self._pending_downloads: dict[tuple[str, str], list[PendingDownload]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

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

    def _parser_advanced(self) -> ParserAdvancedSectionConfig:
        """解析与下载·高级 分组。分组开关关闭时忽略全部字段并使用内置默认值。"""
        return self.config.parser_advanced if self.config.parser_advanced.enabled else ParserAdvancedSectionConfig()

    def _network_advanced(self) -> NetworkAdvancedSectionConfig:
        """网络与下载·高级 分组。分组开关关闭时忽略全部字段并使用内置默认值。"""
        return self.config.network_advanced if self.config.network_advanced.enabled else NetworkAdvancedSectionConfig()

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
        if not self._is_allowed_chat(message):
            return None

        source = self._strip_bot_download_hint(self._message_source(message))
        own_source = self._strip_bot_download_hint(self._message_source(message, include_reply=False))
        session_id = self._session_id(message)
        sender_id = self._get_user_id(message)
        is_download_intent = self._has_download_command(own_source)
        # A link and a download intent in the same message is an explicit direct-download
        # request, not a confirmation for an older pending item in this conversation.
        direct_download = is_download_intent and bool(self._extract_urls(own_source))
        if is_download_intent and not direct_download:
            pending, choices, queue_size, error = self._consume_pending_download(message, own_source)
            if pending is not None:
                pending.delivery_state = "downloading"
                await self._send_text(message, self._download_started_text(pending, queue_size))
                self._create_task(self._download_pending_task(pending, message), "link_resolver.download")
                return {"action": "abort"} if self.config.parser.block_ai_reply else None
            if error:
                await self._send_text(message, error)
                return {"action": "abort"} if self.config.parser.block_ai_reply else None
            if choices:
                await self._send_text(message, self._pending_choice_prompt(choices))
                return {"action": "abort"} if self.config.parser.block_ai_reply else None
            self.ctx.logger.info(
                "Download command has no eligible pending item: session=%s sender=%s",
                session_id,
                sender_id or "unknown",
            )
            await self._send_text(message, "当前会话没有可确认下载的内容，请先发送链接或引用对应的详情卡片。")
            return {"action": "abort"} if self.config.parser.block_ai_reply else None

        # Replies are conversational context, not fresh link shares. Only an exact
        # download command is handled above; all other replies pass to normal chat.
        if self._has_reply(message) or self._is_bot_addressed(message):
            return None

        # The global user lists apply to the person who shared the new link, not
        # to someone who has quoted another sender's existing overview.
        if not self._is_allowed_link_sender(sender_id):
            return None

        candidates = self._extract_urls(source)
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
            if any("b23.tv/" in url or "bilibili.com/" in url for url in candidates):
                self.ctx.logger.warning("Bilibili link ignored: add 'bilibili' to parser.enabled_platforms and reload the plugin")
            return None

        parser, keyword, searched, url = matched
        if self._is_recent(session_id, url):
            return {"action": "abort"} if self.config.parser.block_ai_reply else None

        self.ctx.logger.info("Multi platform link detected: %s %s", parser.platform.display_name, url)
        self._create_task(
            self._process_task(parser, keyword, searched, url, message, direct_download=direct_download),
            "link_resolver.parse",
        )

        if self.config.parser.block_ai_reply:
            return {"action": "abort"}
        return None

    async def _process_task(
        self,
        parser: BaseParser,
        keyword: str,
        searched: Any,
        url: str,
        message: dict[str, Any],
        *,
        direct_download: bool = False,
    ) -> None:
        try:
            result = await parser.parse(keyword, searched)
            if not result.url:
                result.url = url
            if self._parser_advanced().reaction_emoji_enabled and self._get_group_id(message):
                await self._send_emoji_reaction(message.get("message_id", ""), self._parser_advanced().reaction_emoji_id)
            if self.config.parser.download_mode == "automatic" or direct_download:
                if not await self._send_result(message, result):
                    await self._send_text(message, "原媒体下载或发送失败，请稍后重试。")
                return
            self._store_pending_download(message, parser, keyword, searched, url, result.url, result.title)
            await self._send_overview(message, result)
        except Exception as exc:
            self.ctx.logger.warning("Multi platform parser failed for %s: %s", url, exc)
            if isinstance(exc, ModuleNotFoundError) and "_maibot_plugin_" in str(exc):
                await self._send_text(
                    message,
                    "插件状态异常（模块加载失效），请联系管理员重启机器人或重载插件后重试。",
                )
                return
            if isinstance(exc, ParseException):
                await self._send_text(message, exc.message)
                return
            await self._send_text(message, "这个链接解析失败了，请稍后重试。")

    async def _download_pending_task(self, pending: PendingDownload, message: dict[str, Any]) -> None:
        progress = {"downloaded": 0, "total": None}
        started_at = time.monotonic()

        def update_progress(downloaded: int, total: int | None) -> None:
            progress["downloaded"] = downloaded
            progress["total"] = total

        heartbeat = self._create_task(
            self._download_heartbeat(message, progress, started_at), "link_resolver.download_heartbeat"
        )
        callback_token = set_download_progress_callback(update_progress)
        try:
            result = await pending.parser.parse(pending.keyword, pending.searched)
            if not result.url:
                result.url = pending.url
            if not await self._send_result(message, result, include_header=False):
                await self._send_text(message, "原媒体下载或发送失败，请稍后重试。")
                pending.delivery_state = "pending"
            else:
                pending.delivery_state = "sent"
        except Exception as exc:
            pending.delivery_state = "pending"
            self.ctx.logger.warning("Confirmed media download failed for %s: %s", pending.url, exc)
            await self._send_text(message, self._download_error_message(exc))
        finally:
            reset_download_progress_callback(callback_token)
            heartbeat.cancel()

    async def _download_heartbeat(self, message: dict[str, Any], progress: dict[str, int | None], started_at: float) -> None:
        """Report long confirmed downloads without interrupting imminent completions."""
        await asyncio.sleep(60)
        while True:
            elapsed = max(1.0, time.monotonic() - started_at)
            downloaded = int(progress["downloaded"] or 0)
            total = progress["total"]
            if isinstance(total, int) and total > 0 and downloaded > 0:
                remaining = max(0, total - downloaded)
                eta = remaining / (downloaded / elapsed)
                if eta > 20:
                    await self._send_text(
                        message,
                        f"仍在下载：{downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB，预计约 {int(eta)} 秒。",
                    )
            elif downloaded > 0:
                await self._send_text(message, f"仍在下载：已完成 {downloaded / 1024 / 1024:.1f} MB，已等待 {int(elapsed)} 秒。")
            else:
                await self._send_text(message, f"仍在准备下载，已等待 {int(elapsed)} 秒。")
            await asyncio.sleep(45)

    async def _send_overview(self, message: dict[str, Any], result: ParseResult) -> None:
        """Send metadata and one preview only; full media waits for user confirmation."""
        header = self._format_header(result)
        if not self.config.parser.send_preview_image:
            await self._send_text(message, f"{header}\n\n回复“下载”获取原媒体。")
            self._cancel_media_tasks(self._flatten_contents(result))
            return

        preview = await self._get_preview_path(self._flatten_contents(result))
        self._cancel_media_tasks(self._flatten_contents(result), keep_path=preview)
        self._cancel_author_avatar(result)
        if preview is not None:
            if await self._send_overview_hybrid(message, header, preview):
                return

        await self._send_text(message, f"{header}\n\n回复“下载”获取原媒体。")
        if preview is not None:
            await self._send_image(message, preview)

    async def _send_result(self, message: dict[str, Any], result: ParseResult, *, include_header: bool = True) -> bool:
        header = self._format_header(result) if include_header else ""
        text_nodes: list[list[dict[str, Any]]] = []
        media_items = self._flatten_contents(result)
        # Full-media sends never use the author avatar. Close its lazy download coroutine.
        self._cancel_author_avatar(result)
        self._cancel_video_covers(media_items)

        if any(isinstance(content, (VideoContent, DynamicContent)) for content in media_items):
            return await self._send_media_in_order(message, media_items, header)

        if header:
            text_nodes.append([text_segment(header)])

        image_nodes: list[list[dict[str, Any]]] = []
        videos: list[VideoContent | DynamicContent] = []

        image_count = 0
        for content in media_items:
            if isinstance(content, TextContent):
                if content.text:
                    text_nodes.append([text_segment(self._truncate(content.text, self._parser_advanced().max_text_chars))])
            elif isinstance(content, GraphicsContent):
                if content.text:
                    image_nodes.append([text_segment(self._truncate(content.text, self._parser_advanced().max_text_chars))])
                if self._parser_advanced().send_images and image_count < self._parser_advanced().max_images:
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
                if self._parser_advanced().send_images and image_count < self._parser_advanced().max_images:
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
        all_sent = True
        if self._parser_advanced().use_forward_for_multi and len(nodes) > 1:
            forward_mode = "http" if self._parser_advanced().gallery_delivery_mode == "http_forward" else None
            sent_forward = await self._send_group_forward(message, nodes, delivery_mode=forward_mode)

        if not sent_forward:
            if header:
                all_sent = await self._send_text(message, header) and all_sent
            for node in image_nodes:
                segment = node[0]
                if segment.get("type") == "text":
                    all_sent = await self._send_text(message, segment["data"]["text"]) and all_sent
                elif segment.get("type") == "image":
                    all_sent = await self._send_image(message, self._path_from_file_segment(segment)) and all_sent

        if self._parser_advanced().send_video:
            for video in videos[:1]:
                try:
                    path = await video.get_path()
                except Exception as exc:
                    self.ctx.logger.warning("Skipping unavailable video: %s", exc)
                    all_sent = False
                    continue
                all_sent = await self._send_video(message, path) and all_sent
        return all_sent

    async def _send_media_in_order(self, message: dict[str, Any], media_items: list[Any], header: str) -> bool:
        """Send mixed galleries in source order; merged forwards cannot carry videos."""
        all_sent = True
        if header:
            all_sent = await self._send_text(message, header) and all_sent

        image_count = 0
        for content in media_items:
            if isinstance(content, TextContent):
                if content.text:
                    all_sent = await self._send_text(
                        message, self._truncate(content.text, self._parser_advanced().max_text_chars)
                    ) and all_sent
                continue
            if isinstance(content, GraphicsContent) and content.text:
                all_sent = await self._send_text(
                    message, self._truncate(content.text, self._parser_advanced().max_text_chars)
                ) and all_sent

            if isinstance(content, (GraphicsContent, ImageContent)):
                if not self._parser_advanced().send_images or image_count >= self._parser_advanced().max_images:
                    continue
                image_count += 1
                try:
                    path = await content.get_path()
                except Exception as exc:
                    self.ctx.logger.warning("Image %s download failed after retries: %s", image_count, exc)
                    all_sent = await self._send_text(message, f"第 {image_count} 张图片下载失败，已跳过。") and all_sent
                else:
                    all_sent = await self._send_image(message, path) and all_sent
                continue

            if isinstance(content, (VideoContent, DynamicContent)):
                if not self._parser_advanced().send_video:
                    continue
                try:
                    path = await content.get_path()
                except Exception as exc:
                    self.ctx.logger.warning("Dynamic media download failed after retries: %s", exc)
                    await self._send_text(message, self._download_error_message(exc))
                    all_sent = False
                else:
                    all_sent = await self._send_video(message, path) and all_sent
        return all_sent

    async def _send_text(self, message: dict[str, Any], text: str) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] text: %s", text)
            return True
        return await send_text(self.ctx, message, text, self.config.delivery.delivery_mode, self.config.api)

    async def _send_image(self, message: dict[str, Any], path: Path) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] image: path=%s exists=%s", path.name, path.is_file())
            return True
        return await send_image(self.ctx, message, path, self.config.delivery.delivery_mode, self.config.api)

    async def _send_overview_hybrid(self, message: dict[str, Any], header: str, preview: Path) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] overview_hybrid: header=%s preview=%s", header, preview.name)
            return True
        return await send_overview_hybrid(
            self.ctx,
            message,
            header,
            preview,
            self.config.delivery.delivery_mode,
            self.config.api,
        )

    async def _send_video(self, message: dict[str, Any], path: Path) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] video: path=%s exists=%s size=%s", path.name, path.is_file(), path.stat().st_size if path.is_file() else 0)
            return True
        return await send_video(self.ctx, message, path, self.config.delivery.delivery_mode, self.config.api)

    async def _send_group_forward(self, message: dict[str, Any], nodes: list[list[dict[str, Any]]], *, delivery_mode: str | None = None) -> bool:
        if self.config.plugin.dry_run:
            self.ctx.logger.info("[dry_run] group_forward: nodes=%s", len(nodes))
            return True
        return await send_group_forward(self.ctx, message, nodes, delivery_mode or self.config.delivery.delivery_mode, self.config.api)

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
            source_max_size=self._parser_advanced().source_max_size_mb,
            source_max_minute=self._parser_advanced().source_max_minutes,
            debounce_interval=self._parser_advanced().debounce_seconds,
            download_timeout=self._network_advanced().download_timeout,
            download_retry_times=self._network_advanced().download_retry_times,
            common_timeout=self._network_advanced().common_timeout,
            bilibili_cookies=self.config.cookies.bilibili,
            douyin_cookies=self.config.cookies.douyin,
            xhs_cookies=self.config.cookies.xhs,
            xiaoheihe_cookies=self.config.cookies.xiaoheihe,
            use_proxy_platforms=list(self._network_advanced().use_proxy_platforms),
        )

    def _format_header(self, result: ParseResult) -> str:
        parts: list[str] = []
        if result.title:
            parts.append(f"标题：{self._truncate(result.title, 120)}")
        if result.author and result.author.name:
            author = result.author.name
            if result.author.profile_url:
                author += f" ({result.author.profile_url})"
            parts.append(f"作者：{author}")
        if result.text:
            parts.append(self._truncate(result.text, self._parser_advanced().max_text_chars))
        if result.extra_info:
            parts.append(str(result.extra_info))
        if result.url:
            parts.append(f"链接：{self._display_url(result.url)}")
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _display_url(url: str) -> str:
        """Display canonical content URLs without share tracking parameters."""
        parsed = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

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

    def _message_source(self, message: dict[str, Any], *, include_reply: bool = True) -> str:
        base = str(message.get("processed_plain_text", "") or "")
        base = self._strip_mention_noise(self._strip_quoted_reply_text(base, message), message)
        parts = [base]
        for seg in message.get("raw_message", []) or []:
            if not isinstance(seg, dict):
                continue
            if not include_reply and seg.get("type") == "reply":
                continue
            parts.extend(self._urls_from_payload(seg))
        parts.extend(self._urls_from_card_payloads(message))
        return "\n".join(parts)

    @staticmethod
    def _strip_quoted_reply_text(text: str, message: dict[str, Any]) -> str:
        """剔除纯文本中内联的引用回复原文预览。

        MaiBot 把 reply 组件渲染为目标消息的完整预览（``target_message_content``），
        引用 bot 概览卡片时整块文本（含链接、``[image]`` 与下载提示）会被拼进
        ``processed_plain_text``。必须先按 reply 段的原文预览剔除，否则概览内的
        URL 会被当作新一轮直接下载请求，导致引用回复“下载”无法被确认接管。
        """
        for segment in message.get("raw_message", []) or []:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            preview = str(data.get("target_message_content") or "").strip()
            if preview:
                text = text.replace(preview, "")
        return text

    @staticmethod
    def _mention_noise(message: dict[str, Any]) -> list[str]:
        """收集消息中的回复占位与 @提及 占位文本。

        引用回复时 QQ 会自动 @ bot 的群名片，适配器把 reply 段渲染为
        ``[reply]``、at 段渲染为 ``@群名片`` 拼进纯文本，需在命令识别前剔除。
        """
        noise = ["[reply]"]
        for seg in message.get("raw_message", []) or []:
            if not isinstance(seg, dict) or seg.get("type") != "at":
                continue
            data = seg.get("data")
            if not isinstance(data, dict):
                continue
            name = str(
                data.get("target_user_cardname")
                or data.get("target_user_nickname")
                or data.get("target_user_id")
                or ""
            ).strip()
            if name:
                noise.append(f"@{name}")
        return noise

    @classmethod
    def _strip_mention_noise(cls, text: str, message: dict[str, Any]) -> str:
        """剔除消息中的回复占位与 @提及 文本，避免污染下载命令识别。

        先按结构化 ``at`` 段剔除其渲染文本；再兜底剔除开头位置的
        ``@群名片``（旧版或第三方适配器可能把自动 @ 拼成纯文本，无对应 at 段）。
        """
        for token in cls._mention_noise(message):
            text = text.replace(token, "")
        text = re.sub(r"^\s*@\S+(?:\s+@\S+)*\s*", "", text)
        return text.strip()

    @staticmethod
    def _card_payloads(message: dict[str, Any]) -> list[Any]:
        """取消息里的平台卡片负载（NapCat 适配器保留的原始 JSON 卡片）。

        小程序等卡片在 ``raw_message`` 中只保留占位文本和预览图，真实内容链接
        （如 B站卡片的 ``meta.detail_1.qqdocurl``）被适配器移入
        ``message_info.additional_config.platform_card_payloads``，需在此补回。
        """
        additional_config = (message.get("message_info", {}) or {}).get("additional_config", {}) or {}
        if not isinstance(additional_config, dict):
            return []
        payloads = additional_config.get("platform_card_payloads") or message.get("platform_card_payloads") or []
        if not isinstance(payloads, list):
            return []
        return [item for item in payloads if isinstance(item, dict)]

    def _urls_from_card_payloads(self, message: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for card in self._card_payloads(message):
            payload = card.get("payload", {})
            if isinstance(payload, dict):
                urls.extend(self._urls_from_payload(payload))
        return urls

    @staticmethod
    def _strip_bot_download_hint(text: str) -> str:
        """去除 bot 自己附带的下载提示文案，避免被误判为下载意图。"""
        return re.sub(r"回复[“”\"'『「]下载[“”\"'』」]获取原媒体[。.]?", "", text)

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

    @staticmethod
    def _cancel_video_covers(contents: list[Any]) -> None:
        """Full-media sends do not use preview covers, so release their coroutines."""
        for content in contents:
            if not isinstance(content, VideoContent):
                continue
            candidate = content.cover
            if isinstance(candidate, asyncio.Task) and not candidate.done():
                candidate.cancel()
            elif hasattr(candidate, "close"):
                candidate.close()

    def _store_pending_download(
        self,
        message: dict[str, Any],
        parser: BaseParser,
        keyword: str,
        searched: Any,
        url: str,
        result_url: str | None,
        title: str | None,
    ) -> None:
        sender_id = self._get_user_id(message)
        if not sender_id:
            return
        key = (self._session_id(message), sender_id)
        pending = PendingDownload(
            parser=parser,
            keyword=keyword,
            searched=searched,
            url=url,
            reference_urls=tuple(dict.fromkeys(item for item in (url, result_url) if item)),
            title=self._truncate(title or "未命名内容", 40),
            expires_at=time.time() + self._parser_advanced().pending_download_ttl_seconds,
        )
        entries = [entry for entry in self._pending_downloads.get(key, []) if entry.expires_at >= time.time() and entry.url != url]
        entries.append(pending)
        self._pending_downloads[key] = entries

    def _consume_pending_download(
        self, message: dict[str, Any], source: str
    ) -> tuple[PendingDownload | None, list[PendingDownload], int, str | None]:
        session_id = self._session_id(message)
        sender_id = self._get_user_id(message)
        if not sender_id:
            return None, [], 0, None
        key = (session_id, sender_id)
        entries = [
            entry
            for entry in self._pending_downloads.get(key, [])
            if entry.expires_at >= time.time() and self._is_allowed_link_sender(sender_id)
        ]
        reply_urls = self._reply_urls(message)

        # A QQ reply contains the referenced bot message preview. Only consume the
        # pending entry whose original or resolved URL is present in that preview.
        if reply_urls:
            matched = [entry for entry in entries if set(entry.reference_urls).intersection(reply_urls)]
            matched_queue_size = len(entries)
            if not matched and self._can_download_for_others(message):
                for candidate_key, candidate_entries in self._pending_downloads.items():
                    if candidate_key[0] != session_id or candidate_key == key:
                        continue
                    if not self._is_allowed_link_sender(candidate_key[1]):
                        continue
                    active = [entry for entry in candidate_entries if entry.expires_at >= time.time()]
                    candidate_matches = [
                        entry for entry in active if set(entry.reference_urls).intersection(reply_urls)
                    ]
                    if candidate_matches:
                        if matched or len(candidate_matches) > 1:
                            return None, [], 0, "引用的详情卡片对应多条待下载内容，请由原发送者确认下载。"
                        matched = candidate_matches
                        matched_queue_size = len(active)
            if len(matched) == 1:
                pending = matched[0]
                if pending.delivery_state == "sent":
                    return None, [], 0, "该详情卡片的媒体已成功发送，无需重复下载。"
                if pending.delivery_state == "downloading":
                    return None, [], 0, "该详情卡片的媒体正在下载，请勿重复请求。"
                return pending, [], matched_queue_size, None
            if not matched:
                if not self._is_allowed_link_sender(sender_id) and any(
                    set(entry.reference_urls).intersection(reply_urls)
                    for entry in self._pending_downloads.get(key, [])
                ):
                    return None, [], 0, None
                return None, [], len(entries), "引用的详情卡片不属于你的待下载内容，无法下载。"
            return None, matched, len(entries), None

        if not entries:
            self._pending_downloads.pop(key, None)
            return None, [], 0, None

        if selected := re.search(rf"{self._download_intent_pattern()}\s*(?P<num>[1-9]\d*)", source):
            index = int(selected.group("num")) - 1
            if 0 <= index < len(entries):
                pending = entries.pop(index)
                if entries:
                    self._pending_downloads[key] = entries
                else:
                    self._pending_downloads.pop(key, None)
                return pending, [], len(entries) + 1, None

        matched = [entry for entry in entries if entry.url in source]
        if len(matched) == 1:
            pending = matched[0]
            entries.remove(pending)
            if entries:
                self._pending_downloads[key] = entries
            else:
                self._pending_downloads.pop(key, None)
            return pending, [], len(entries) + 1, None
        if len(entries) == 1:
            self._pending_downloads.pop(key, None)
            return entries[0], [], 1, None
        if self._parser_advanced().pending_download_selection == "latest":
            pending = entries.pop()
            if entries:
                self._pending_downloads[key] = entries
            else:
                self._pending_downloads.pop(key, None)
            return pending, [], len(entries) + 1, None
        self._pending_downloads[key] = entries
        return None, entries, len(entries), None

    def _reply_urls(self, message: dict[str, Any]) -> set[str]:
        urls: set[str] = set()
        for segment in message.get("raw_message") or []:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                urls.update(self._urls_from_payload(data.get("target_message_content", "")))
        return urls

    def _can_download_for_others(self, message: dict[str, Any]) -> bool:
        """是否允许群成员引用 Bot 详情卡片代为下载其他发送者的任务。"""
        advanced = self._parser_advanced()
        group_id = self._get_group_id(message)
        user_id = self._get_user_id(message)
        if not group_id or not self._is_reply_to_bot(message):
            return False
        mode = advanced.cross_sender_download_access_mode
        if mode == "disabled":
            return False
        group_allow = {str(item).strip() for item in advanced.cross_sender_download_group_whitelist if str(item).strip()}
        user_allow = {str(item).strip() for item in advanced.cross_sender_download_user_whitelist if str(item).strip()}
        explicitly_delegated = bool((group_id and group_id in group_allow) or (user_id and user_id in user_allow))

        # The global link-sender lists also protect a requester by default. An
        # explicit delegated-download allowlist entry is the only override.
        if not self._is_allowed_link_sender(user_id) and not explicitly_delegated:
            return False
        if mode == "whitelist":
            return explicitly_delegated
        group_block = {str(item).strip() for item in advanced.cross_sender_download_group_blacklist if str(item).strip()}
        user_block = {str(item).strip() for item in advanced.cross_sender_download_user_blacklist if str(item).strip()}
        return not bool((group_id and group_id in group_block) or (user_id and user_id in user_block))

    def _download_intent_pattern(self) -> str:
        default = r"下载"
        custom = (self._parser_advanced().download_intent_regex or "").strip()
        return f"(?:{default}|{custom})" if custom else f"(?:{default})"

    def _is_download_confirmation(self, text: str) -> bool:
        return bool(re.fullmatch(rf"{self._download_intent_pattern()}\s*(?:[1-9]\d*)?", text.strip()))

    def _has_download_command(self, text: str) -> bool:
        """消息中是否有独立一行的下载命令，而非普通语句中的“下载”字样。"""
        return any(self._is_download_confirmation(line) for line in text.splitlines() if line.strip())

    @staticmethod
    def _has_reply(message: dict[str, Any]) -> bool:
        return any(
            isinstance(segment, dict) and segment.get("type") == "reply"
            for segment in message.get("raw_message") or []
        )

    @staticmethod
    def _pending_choice_prompt(entries: list[PendingDownload]) -> str:
        lines = ["有多条待下载内容，请回复“下载 序号”选择："]
        lines.extend(f"{index}. {entry.title}" for index, entry in enumerate(entries, 1))
        return "\n".join(lines)

    @staticmethod
    def _download_started_text(pending: PendingDownload, queue_size: int) -> str:
        if queue_size <= 1:
            return "好的，开始下载。"
        return f"好的，开始下载：{pending.title}\n当前待下载队列共 {queue_size} 条，剩余 {queue_size - 1} 条。"

    @staticmethod
    def _download_error_message(exc: Exception) -> str:
        detail = str(exc)
        if "ffmpeg" in detail.lower() and ("未安装" in detail or "not found" in detail.lower()):
            return (
                "媒体需要 FFmpeg 合并音视频，但 MaiBot Runner 未安装 FFmpeg。\n"
                "请在 MaiBot 使用的 Docker 镜像中预装 ffmpeg 并重建容器；不要只在运行中的容器内临时安装。"
            )
        return "媒体下载失败，请稍后重试。"

    def _create_task(self, coroutine: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _session_id(self, message: dict[str, Any]) -> str:
        return str(message.get("session_id", "") or self._group_or_user_id(message) or "unknown")

    def _is_allowed_chat(self, message: dict[str, Any]) -> bool:
        """应用群聊和私聊范围名单，不检查当前发言者的全局链接权限。"""
        group_id = self._get_group_id(message)
        user_id = self._get_user_id(message)

        def norm(items: list[str]) -> set[str]:
            return {str(item).strip() for item in items if str(item).strip()}

        group_allow = norm(self._parser_advanced().group_whitelist)
        group_block = norm(self._parser_advanced().group_blacklist)
        private_allow = norm(self._parser_advanced().private_whitelist)
        private_block = norm(self._parser_advanced().private_blacklist)

        if group_id:
            if group_id in group_block:
                return False
            if group_allow and group_id not in group_allow:
                return False
        else:
            if user_id and user_id in private_block:
                return False
            if private_allow and (not user_id or user_id not in private_allow):
                return False

        return True

    def _is_allowed_link_sender(self, sender_id: str | None) -> bool:
        """检查链接原发送者的全局黑白名单，黑名单优先。"""
        user_allow = {str(item).strip() for item in self._parser_advanced().user_whitelist if str(item).strip()}
        user_block = {str(item).strip() for item in self._parser_advanced().user_blacklist if str(item).strip()}
        if not sender_id:
            return not user_allow
        if sender_id in user_block:
            return False
        return not user_allow or sender_id in user_allow

    def _is_allowed_message(self, message: dict[str, Any]) -> bool:
        """兼容入口测试：检查聊天范围和当前链接发送者的全局权限。"""
        return self._is_allowed_chat(message) and self._is_allowed_link_sender(self._get_user_id(message))

    def _is_recent(self, session_id: str, url: str) -> bool:
        interval = int(self._parser_advanced().debounce_seconds)
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
    def _get_self_id(message: dict[str, Any]) -> str | None:
        message_info = message.get("message_info", {})
        additional_config = message_info.get("additional_config", {}) if isinstance(message_info, dict) else {}
        self_id = additional_config.get("self_id") if isinstance(additional_config, dict) else None
        return str(self_id) if self_id else None

    def _is_bot_addressed(self, message: dict[str, Any]) -> bool:
        """消息是否明确指向当前 bot。

        以 ``is_mentioned`` 为标准：引用回复 bot 时 QQ 会自动 @ bot 群名片，
        适配器据此置位；``is_at`` 是其兼容别名。其他适配器再回退到回复目标
        发送者判断。
        """
        if message.get("is_mentioned") or message.get("is_at"):
            return True
        self_id = self._get_self_id(message)
        if not self_id:
            return False
        for segment in message.get("raw_message") or []:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            target_sender = str(data.get("target_message_sender_id") or "").strip()
            if target_sender == self_id:
                return True
        return False

    def _is_reply_to_bot(self, message: dict[str, Any]) -> bool:
        """判断是否引用当前 Bot 发出的详情卡片。"""
        return self._is_bot_addressed(message)

    @staticmethod
    def _get_user_id(message: dict[str, Any]) -> str | None:
        message_info = message.get("message_info", {})
        user_info = message_info.get("user_info", {}) if isinstance(message_info, dict) else {}
        user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
        return str(user_id) if user_id else None


def create_plugin() -> MultiPlatformParserPlugin:
    return MultiPlatformParserPlugin()
