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
    config_version: str = Field(default="0.1.2", description="配置文件版本", json_schema_extra=_ui("配置版本", "由插件用于补齐配置字段，请勿手动修改。", 1, label_en="Configuration version", hint_en="Used to merge configuration fields. Do not edit manually.", label_ja="設定バージョン", hint_ja="設定項目の統合に使用します。手動で変更しないでください。", disabled=True))
    version: str = Field(default="0.1.2", description="插件版本", json_schema_extra=_ui("插件版本", "当前插件版本，仅供查看。", 2, label_en="Plugin version", hint_en="Current plugin version. Read only.", label_ja="プラグインバージョン", hint_ja="現在のプラグインバージョンです。読み取り専用です。", disabled=True))
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
    group_whitelist: list[str] = Field(default_factory=list, description="自动解析群白名单", json_schema_extra=_ui("自动解析群白名单", "仅这些 QQ 群自动解析；留空表示所有群。填入群号字符串。", 1, label_en="Group allowlist", hint_en="Only parse automatically in these QQ groups. Leave empty for all groups. Enter group IDs as strings.", label_ja="グループ許可リスト", hint_ja="指定した QQ グループでのみ自動解析します。空欄は全グループです。グループ ID を文字列で入力します。"))
    block_ai_reply: bool = Field(default=True, description="阻止普通 AI 回复", json_schema_extra=_ui("阻止普通 AI 回复", "链接命中后中止 MaiBot 常规聊天处理，避免链接解析和 AI 回复重复发送。", 2, label_en="Block normal AI reply", hint_en="Stops regular MaiBot chat handling after a matching link to avoid duplicate parser and AI replies.", label_ja="通常の AI 返信を停止", hint_ja="リンク検出後に通常のチャット処理を止め、解析結果との重複返信を避けます。"))
    debounce_seconds: int = Field(default=120, description="链接去重时间", ge=0, json_schema_extra=_ui("链接去重时间（秒）", "同一会话内重复发送同一链接时的忽略时长；设为 0 禁用去重。", 3, label_en="Link deduplication (seconds)", hint_en="Ignore repeated copies of the same link in one session. Set 0 to disable.", label_ja="リンク重複除外（秒）", hint_ja="同一セッション内で同じリンクを再送した場合の無視時間です。0 で無効化します。"))
    max_images: int = Field(default=9, description="单条链接最多发送图片数", ge=1, le=30, json_schema_extra=_ui("最多发送图片数", "完整图集发送时的最大图片数；超过部分不会发送。", 4, label_en="Maximum images", hint_en="Maximum images sent for one link. Extra images are skipped.", label_ja="最大画像数", hint_ja="1 リンクで送信する画像数の上限です。超過分は送信しません。"))
    max_text_chars: int = Field(default=700, description="摘要正文最大字符数", ge=80, le=3000, json_schema_extra=_ui("摘要最大字符数", "过长的标题、正文或摘要会被截断。", 5, label_en="Maximum summary characters", hint_en="Long titles, body text, and summaries are truncated.", label_ja="概要の最大文字数", hint_ja="長いタイトル、本文、概要は切り詰められます。"))
    send_images: bool = Field(default=True, description="是否发送图片", json_schema_extra=_ui("发送图片", "关闭后图集和图片媒体不会发送；视频封面预览仍由“发送预览图”控制。", 6, label_en="Send images", hint_en="When disabled, galleries and image media are not sent. Video preview is controlled separately.", label_ja="画像を送信", hint_ja="無効時は画像とギャラリーを送信しません。動画プレビューは別設定です。"))
    send_video: bool = Field(default=True, description="是否发送视频", json_schema_extra=_ui("发送视频", "关闭后仍可解析并发送概览，但确认下载后不会回传视频。", 7, label_en="Send videos", hint_en="When disabled, videos are parsed and summarized but are not sent after confirmation.", label_ja="動画を送信", hint_ja="無効時も解析と概要送信は行いますが、確認後に動画は送信しません。"))
    use_forward_for_multi: bool = Field(default=True, description="群聊多图使用合并转发", json_schema_extra=_ui("群聊多图使用合并转发", "仅群聊生效。关闭后图片和失败占位符将逐条发送。", 8, label_en="Use group forward for galleries", hint_en="Groups only. When disabled, images and failure placeholders are sent one by one.", label_ja="ギャラリーを結合転送で送信", hint_ja="グループのみ有効です。無効時は画像と失敗表示を個別に送信します。"))
    download_mode: Literal["confirm", "automatic"] = Field(default="confirm", description="下载模式", json_schema_extra={**_ui("下载模式", "确认下载更适合生产群聊：先发概览，只有原发送者回复“下载”才下载完整媒体。自动下载会立即消耗带宽和存储。", 9, label_en="Download mode", hint_en="Confirm is safer for groups: send an overview first and download only after the original sender replies. Automatic download immediately uses bandwidth and storage.", label_ja="ダウンロードモード", hint_ja="確認モードはグループ向けです。概要を送信後、元の送信者の確認でダウンロードします。自動モードは即座に通信量とストレージを消費します。"), "widget": "select", "options": [{"label": "确认后下载", "value": "confirm"}, {"label": "自动下载", "value": "automatic"}]})
    pending_download_ttl_seconds: int = Field(default=600, description="等待确认有效期", ge=60, le=3600, json_schema_extra=_ui("等待确认有效期（秒）", "确认下载任务过期后需要重新发送链接。", 10, label_en="Confirmation expiry (seconds)", hint_en="After expiration, send the link again to create a new download request.", label_ja="確認の有効期限（秒）", hint_ja="期限切れ後は、新しいダウンロード要求を作るためにリンクを再送してください。"))
    send_preview_image: bool = Field(default=True, description="是否发送封面或首图预览", json_schema_extra=_ui("发送封面或首图预览", "确认模式下下载视频封面或图集首图，用于让用户在完整下载前确认内容。", 11, label_en="Send cover or first-image preview", hint_en="In confirm mode, download a video cover or first gallery image so users can verify content before the full download.", label_ja="カバーまたは先頭画像のプレビューを送信", hint_ja="確認モードでは、完全なダウンロード前に内容を確認できるよう動画カバーまたは先頭画像を取得します。"))
    reaction_emoji_enabled: bool = Field(default=True, description="识别后表情回应", json_schema_extra=_ui("识别后表情回应", "对群聊原消息调用 NapCat 表情回应 API；私聊不会执行。", 12, label_en="React after recognition", hint_en="Calls NapCat's reaction API for the original group message. It is not used in private chats.", label_ja="認識後にリアクション", hint_ja="元のグループメッセージに NapCat のリアクション API を使用します。個別チャットでは実行しません。"))
    reaction_emoji_id: int = Field(default=76, description="QQ 表情回应 ID", ge=1, json_schema_extra=_ui("QQ 表情回应 ID", "NapCat `set_msg_emoji_like` 使用的数字表情 ID。仅在已启用表情回应时生效。", 13, label_en="QQ reaction emoji ID", hint_en="Numeric emoji ID for NapCat set_msg_emoji_like. Used only when reactions are enabled.", label_ja="QQ リアクション絵文字 ID", hint_ja="NapCat set_msg_emoji_like に使用する数値 ID です。リアクション有効時のみ使用します。"))
    source_max_size_mb: int = Field(default=80, description="单媒体最大下载大小", ge=1, le=300, json_schema_extra=_ui("单媒体最大下载大小（MB）", "单个图片、视频或音频超过此值会被拒绝下载。提高该值会增加磁盘、网络和 QQ 发送失败风险。", 14, label_en="Maximum media size (MB)", hint_en="Images, videos, or audio above this size are not downloaded. Increasing it raises disk, bandwidth, and QQ delivery failure risk.", label_ja="メディア最大サイズ（MB）", hint_ja="このサイズを超える画像、動画、音声はダウンロードしません。増やすとディスク、通信量、QQ 送信失敗のリスクが上がります。"))
    source_max_minutes: int = Field(default=8, description="视频最大时长", ge=1, le=60, json_schema_extra=_ui("视频最大时长（分钟）", "超过此时长的视频不下载，避免意外的大文件任务。", 15, label_en="Maximum video duration (minutes)", hint_en="Longer videos are not downloaded to avoid unexpected large jobs.", label_ja="動画の最大時間（分）", hint_ja="予期しない大きなタスクを避けるため、この時間を超える動画はダウンロードしません。"))


class NetworkSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "网络与下载"
    __ui_icon__: ClassVar[str] = "network_check"
    __ui_order__: ClassVar[int] = 2

    proxy: str = Field(default="", description="解析/下载代理", json_schema_extra=_ui("解析/下载代理", "可选。填写 MaiBot 容器可访问的 HTTP/HTTPS 代理地址，例如 http://proxy:7890。留空直连；不要填写宿主机的 127.0.0.1，除非代理也在 MaiBot 容器内。", 0, label_en="Parser/download proxy", hint_en="Optional HTTP/HTTPS proxy reachable from the MaiBot container, e.g. http://proxy:7890. Leave empty for direct access. Do not use host 127.0.0.1 unless the proxy runs inside the MaiBot container.", label_ja="解析・ダウンロードプロキシ", hint_ja="任意。MaiBot コンテナから到達できる HTTP/HTTPS プロキシを指定します。空欄は直接接続です。プロキシが同一コンテナ内でない限り、ホストの 127.0.0.1 は使用しないでください。", placeholder="http://proxy:7890"))
    use_proxy_platforms: list[Literal["douyin", "xhs", "xiaoheihe", "bilibili"]] = Field(default_factory=list, description="使用代理的平台", json_schema_extra={**_ui("使用代理的平台", "仅选中的平台通过上方代理访问。未选择的平台保持直连。", 1, label_en="Platforms using proxy", hint_en="Only selected platforms use the proxy above. Others connect directly.", label_ja="プロキシを使用するプラットフォーム", hint_ja="選択したプラットフォームのみ上記プロキシを使用します。その他は直接接続です。"), "widget": "checkboxes", "options": [{"label": "抖音", "value": "douyin"}, {"label": "小红书", "value": "xhs"}, {"label": "小黑盒", "value": "xiaoheihe"}, {"label": "B站", "value": "bilibili"}]})
    common_timeout: int = Field(default=30, description="解析请求超时", ge=5, le=120, json_schema_extra=_ui("解析请求超时（秒）", "页面解析、短链跳转等普通网络请求的总超时。网络较慢时可适当提高。", 2, label_en="Parser request timeout (seconds)", hint_en="Total timeout for page parsing and short-link redirects. Increase slightly on slow networks.", label_ja="解析リクエストのタイムアウト（秒）", hint_ja="ページ解析と短縮リンクのリダイレクトに使う合計タイムアウトです。低速回線では少し増やせます。"))
    download_timeout: int = Field(default=120, description="媒体下载超时", ge=10, le=600, json_schema_extra=_ui("媒体下载超时（秒）", "单次媒体下载请求的总超时。大视频或低速代理可能需要更高值。", 3, label_en="Media download timeout (seconds)", hint_en="Total timeout for one media download request. Large videos or slow proxies may need a higher value.", label_ja="メディアダウンロードのタイムアウト（秒）", hint_ja="1 回のメディアダウンロードの合計タイムアウトです。大きな動画や低速プロキシでは増やす必要があります。"))
    download_retry_times: int = Field(default=1, description="媒体下载重试次数", ge=0, le=5, json_schema_extra=_ui("媒体下载重试次数", "网络中断、响应不完整等可恢复错误的额外重试次数。重试会增加耗时；抖音失效 CDN 链接可能仍需重新解析。", 4, label_en="Media download retries", hint_en="Extra retries for recoverable errors such as interrupted or incomplete responses. Retries increase latency; expired Douyin CDN links may still require a new parse.", label_ja="メディアダウンロードの再試行回数", hint_ja="中断や不完全な応答など、回復可能なエラーの追加再試行回数です。再試行は時間を要し、期限切れの Douyin CDN リンクは再解析が必要な場合があります。"))


class CookieSectionConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "平台 Cookie"
    __ui_icon__: ClassVar[str] = "cookie"
    __ui_order__: ClassVar[int] = 3

    douyin: str = Field(default="", description="抖音 Cookie", json_schema_extra=_ui("抖音 Cookie", "可选。仅在公开解析受限或下载被拒绝时填写浏览器导出的 Cookie。属于敏感凭据，请勿提交、截图或分享。", 0, label_en="Douyin cookie", hint_en="Optional. Use a browser-exported cookie only when public parsing is limited or downloads are rejected. This is sensitive; do not commit, screenshot, or share it.", label_ja="Douyin Cookie", hint_ja="任意。公開解析やダウンロードが拒否される場合のみ、ブラウザからエクスポートした Cookie を使用します。機密情報のため、コミット、スクリーンショット、共有は禁止です。", input_type="password", multiline=True))
    xhs: str = Field(default="", description="小红书 Cookie", json_schema_extra=_ui("小红书 Cookie", "可选。用于提高公开笔记解析稳定性；应从本人浏览器导出，且不得提交到 Git。", 1, label_en="Xiaohongshu cookie", hint_en="Optional. Improves public-note parsing stability. Export from your own browser and never commit it to Git.", label_ja="Xiaohongshu Cookie", hint_ja="任意。公開ノート解析の安定性を改善します。自身のブラウザからエクスポートし、Git にコミットしないでください。", input_type="password", multiline=True))
    xiaoheihe: str = Field(default="", description="小黑盒 Cookie", json_schema_extra=_ui("小黑盒 Cookie", "可选。只有公开内容无法解析时才需要填写。", 2, label_en="Xiaoheihe cookie", hint_en="Optional. Required only when public content cannot be parsed.", label_ja="Xiaoheihe Cookie", hint_ja="任意。公開コンテンツを解析できない場合のみ必要です。", input_type="password", multiline=True))
    bilibili: str = Field(default="", description="B站 Cookie", json_schema_extra=_ui("B站 Cookie", "可选。启用 B站平台后，可用于访问更高画质或受游客限制的内容。必须使用自己的 Cookie，并注意登录态过期。", 3, label_en="Bilibili cookie", hint_en="Optional. When Bilibili is enabled, it may allow higher quality or guest-restricted content. Use only your own cookie and expect sessions to expire.", label_ja="Bilibili Cookie", hint_ja="任意。Bilibili を有効化した場合、高画質やゲスト制限コンテンツに使用できます。自身の Cookie のみを使い、期限切れに注意してください。", input_type="password", multiline=True))


class ApiConfig(PluginConfigBase):
    __ui_label__: ClassVar[str] = "NapCat OneBot HTTP"
    __ui_icon__: ClassVar[str] = "api"
    __ui_order__: ClassVar[int] = 4

    host: str = Field(default="127.0.0.1", description="OneBot HTTP API 主机", json_schema_extra=_ui("OneBot HTTP API 主机", "NapCat HTTP API 地址。容器部署时填写 NapCat 服务名或容器 IP；127.0.0.1 指向 MaiBot 容器自身，通常不是 NapCat。", 0, label_en="OneBot HTTP API host", hint_en="NapCat HTTP API address. In Docker, use the NapCat service name or container IP; 127.0.0.1 points to the MaiBot container itself and is usually not NapCat.", label_ja="OneBot HTTP API ホスト", hint_ja="NapCat HTTP API のアドレスです。Docker では NapCat のサービス名またはコンテナ IP を使用します。127.0.0.1 は MaiBot コンテナ自身を指すため、通常は NapCat ではありません。", placeholder="napcat"))
    port: int = Field(default=3000, description="OneBot HTTP API 端口", ge=1, le=65535, json_schema_extra=_ui("OneBot HTTP API 端口", "与 NapCat 正向 HTTP 服务监听端口一致，不是 WebSocket 端口。", 1, label_en="OneBot HTTP API port", hint_en="Must match NapCat's forward HTTP listening port, not its WebSocket port.", label_ja="OneBot HTTP API ポート", hint_ja="NapCat の順方向 HTTP 待受ポートと一致させます。WebSocket ポートではありません。"))
    token: str = Field(default="", description="OneBot HTTP API Token", json_schema_extra=_ui("OneBot HTTP API Token", "NapCat 启用访问令牌时填写相同 Token；留空表示不使用鉴权。属于敏感凭据，请勿提交或写入日志。", 2, label_en="OneBot HTTP API token", hint_en="Enter the same token when NapCat access-token authentication is enabled. Leave empty when no authentication is used. This is sensitive; do not commit or log it.", label_ja="OneBot HTTP API トークン", hint_ja="NapCat のアクセストークン認証を有効にした場合は同じトークンを入力します。認証なしなら空欄です。機密情報のため、コミットやログ出力は禁止です。", input_type="password"))
    bot_uin: str = Field(default="", description="合并转发 Bot QQ", json_schema_extra=_ui("合并转发 Bot QQ", "群合并转发节点显示的发送者 QQ 号。建议填写当前 NapCat 登录的机器人 QQ；留空会使用兼容默认值。", 3, label_en="Forward-message bot QQ", hint_en="QQ number displayed as the sender of group-forward nodes. Use the QQ account logged into NapCat; an empty value uses a compatibility default.", label_ja="結合転送の Bot QQ", hint_ja="グループ結合転送ノードの送信者として表示する QQ 番号です。NapCat にログインしている Bot QQ を使用します。空欄時は互換性用の既定値を使用します。", placeholder="123456789"))


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
