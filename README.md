# MaiBot 社媒聚合解析

面向 MaiBot 的社交媒体链接聚合解析插件。收到抖音、小红书、小黑盒等链接后，插件先发送内容概览和封面或首图预览；默认由原链接发送者在同一会话确认后，才下载并发送完整媒体。

**仓库**：https://github.com/Powdered-Delta/maibot-social-link-parser

## 特性与进度

- [x] 抖音短链、视频、图集解析。
- [x] 小红书短链、图文、视频解析，支持 `http://xhslink.cn` 分享链接。
- [x] 小黑盒帖子和游戏分享解析。
- [x] B站解析兼容，默认关闭，避免与独立 B站插件重复处理。
- [x] 解析摘要、视频封面或图集首图预览。
- [x] 识别链接后对 QQ 原消息添加表情回应。
- [x] 默认确认下载：仅同会话、同发送者可确认并取得完整媒体。
- [x] 图集部分成功发送：单张图片多次下载失败后保留其他成功图片，并插入缺失项文本占位符。
- [x] `.part` 文件与 HTTP Range 续传，改善大媒体中断重试。
- [x] `dry_run` 演练模式：不实际发送 QQ 消息，仍执行识别、概览和下载流程。
- [ ] 支持共享 Docker volume 的媒体路径映射，解决 MaiBot 与 NapCat 双容器下的 `file://` 媒体可见性问题。
- [ ] 支持 SOCKS5/SOCKS5h 代理，并为 HTTP/SOCKS 代理增加独立认证字段与凭据 URL 编码，避免将密码直接拼入代理 URL。
- [ ] 建立面向用户的错误分类和提示：区分 Cookie/Token 缺失、非公开或无权限内容、平台风控、链接失效、媒体超限、下载失败与 QQ 投递失败；保留底层错误日志用于诊断。
- [ ] 为确认下载状态机补充显式命令，例如 `/链接解析下载 <链接>`，用于多条待下载任务时的无歧义选择。
- [ ] 增加单元测试，覆盖小红书 `.cn` 短链、确认下载权限隔离、Range 续传和图集失败占位符。
- [ ] 接入并修复 X/Twitter：优先使用现有 xdown 路径，补齐 `x.com`、`twitter.com`、`mobile.twitter.com` 识别；yt-dlp 作为可选后备。
- [ ] 增加可配置的下载意图正则、群聊/私聊/用户黑白名单，并保持默认确认下载策略。

## 支持平台

| 平台 | 链接识别 | 概览与预览 | 确认下载 | 默认状态 |
| --- | --- | --- | --- | --- |
| 抖音 | 短链、视频、图集 | 是 | 是 | 启用 |
| 小红书 | 短链、图文、视频 | 是 | 是 | 启用 |
| 小黑盒 | 帖子、游戏分享 | 是 | 视来源媒体而定 | 启用 |
| B站 | 视频、图文等 | 是 | 是 | 关闭 |

启用 B站前，应停用或禁用当前会话中的其他 B站链接解析插件，避免重复回复。

## 参考项目

本插件借鉴或复用了下列开源项目的思路与代码。各项目的版权与许可仍归其原作者所有。

| 项目 | 用途 | 许可证 |
| --- | --- | --- |
| [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser) | 多平台链接解析核心 | MIT |
| [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 部分平台的媒体信息提取与下载 | Unlicense |
| [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot) | 插件运行平台与 SDK | 见上游仓库 |
| [Mai-with-u/MaiBot-Napcat-Adapter](https://github.com/Mai-with-u/MaiBot-Napcat-Adapter) | 后续 QQ/NapCat WebSocket 发送适配参考 | GPL-3.0-or-later |

其中 `astrbot_plugin_parser` 的解析核心已被移植到本项目。其原始 MIT 许可证文本和版权声明保留在仓库的 [LICENSE](LICENSE) 文件中；具体复用范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目新增的 MaiBot 适配、确认下载、预览、演练模式、下载续传和图集失败占位符逻辑由本仓库维护。

## 工作流程

```text
用户发送社媒链接
  -> 识别平台与解析元数据
  -> 表情回应
  -> 发送摘要与封面/首图预览
  -> 保存同会话、同用户的待下载任务
  -> 用户回复“下载”
  -> 下载完整媒体并发送图集/视频
```

默认下载模式为 `confirm`。支持链接的发送者可在同一会话内回复“下载”“帮我下载”“发视频”或“保存”确认下载。其他用户、其他会话或过期确认不会消费该待下载任务。

设置 `parser.download_mode = "automatic"` 可改为识别成功后自动下载，但不建议在生产群聊中默认启用。

## 安装

将插件克隆到 MaiBot 容器中 `/MaiMBot/plugins` 对应的宿主机挂载目录：

```bash
cd /path/to/MaiMBot/plugins
git clone git@github.com:Powdered-Delta/maibot-social-link-parser.git
```

最终容器内路径应为：

```text
/MaiMBot/plugins/maibot-social-link-parser/
```

重载 MaiBot 插件或重启 MaiBot 容器后，在插件管理页启用本插件。首次加载时，Runner 会根据 `plugin.py` 中的配置模型生成 `config.toml`。

不要提交 `config.toml`、Cookie、下载缓存、媒体文件或 OneBot Token。

## 配置

`config.example.toml` 提供了配置参考。常用字段如下：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `plugin.enabled` | `true` | 是否启用插件 |
| `plugin.dry_run` | `false` | 演练模式，不实际发送 QQ 消息 |
| `parser.enabled_platforms` | 抖音、小红书、小黑盒 | WebUI 多选平台开关；B站默认关闭 |
| `parser.download_mode` | `confirm` | `confirm` 为确认下载，`automatic` 为自动下载 |
| `parser.gallery_delivery_mode` | `default` | 静态图集默认跟随 QQ 通道；可设为 `http_forward`，经 OneBot HTTP 合并转发发送 |
| `parser.pending_download_ttl_seconds` | `600` | 等待用户确认下载的有效期，单位秒 |
| `parser.pending_download_selection` | `latest` | 多条待下载内容时，`latest` 让“下载”选择最新一条；`number` 要求“下载 1”等明确编号 |
| `parser.send_preview_image` | `true` | 概览是否发送视频封面或图集首图 |
| `parser.reaction_emoji_enabled` | `true` | 识别链接后是否添加 QQ 表情回应 |
| `parser.group_whitelist` | `[]` | 自动解析群白名单；空列表表示所有群 |
| `parser.block_ai_reply` | `true` | 命中链接后是否中止 MaiBot 普通聊天流程 |
| `network.download_retry_times` | `3` | 媒体下载重试次数 |
| `delivery.delivery_mode` | `websocket` | 默认经 MaiBot 的 NapCat WebSocket 网关发送；可改为 `http` 使用兼容直连 |
| `api.host`、`api.port`、`api.token` | - | 仅 `delivery.delivery_mode = "http"` 时使用的 NapCat OneBot HTTP 配置 |

默认通过 MaiBot 已启用的 NapCat WebSocket 网关发送，配置页的“HTTP 兼容后备”分组无需填写。选择 HTTP 兼容模式时，才需要让 `api.host`、`api.port`、`api.token` 指向 MaiBot 容器可访问的 NapCat OneBot HTTP 服务。

## 本地演练模式

设置 `plugin.dry_run = true` 后，插件不连接 OneBot HTTP、NapCat 或 QQ，不发送文本、图片、视频、合并转发和表情回应；这些操作会记录为 `[dry_run]` 日志。链接识别、Hook 中止、概览生成、待确认任务、封面/首图下载和用户确认后的完整媒体下载仍会执行，适合不接 NapCat 的本地 MaiBot 场景 1 验证。

`dry_run` 只禁止出站副作用，不会创造入站消息。要在 MaiBot 实际信息流中验证 `chat.receive.after_process` Hook，仍需接入一个消息网关。可选择连接 NapCat 测试账号，或安装专门的本地测试网关，将 HTTP/CLI 构造的测试消息注入 MaiBot；单独启动 MaiBot 而没有入站网关，只能验证插件加载和配置，不能触发场景 1。

场景 1 的期望日志顺序：

1. 发送包含支持链接的测试消息后出现 `Multi platform link detected`。
2. 出现 `[dry_run] text:`，内容包含概览和“回复‘下载’获取原媒体”。
3. 出现 `[dry_run] image:`，对应视频封面或图集首图。
4. 同一 `session_id`、同一 `user_id` 发送“下载”后出现 `[dry_run] text: 好的，开始下载。`。
5. 下载完成后出现 `[dry_run] group_forward:`、`[dry_run] image:` 或 `[dry_run] video:`；不会产生任何 QQ/NapCat 网络请求。

## 已知边界

- 默认 QQ 发送通过 MaiBot 的 NapCat WebSocket 网关；HTTP 是可选兼容模式。WebSocket 模式下图集逐条发送，HTTP 模式可使用合并转发。
- 图集发送保留下载到的源文件；MaiBot 聊天记录整合只消费摘要和元数据，不参与原图再编码。
- NapCat 与 MaiBot 分属不同容器时，`file://` 媒体路径必须对 NapCat 可见。生产环境需配置共享 volume 或保留现有可用的媒体路径方案。
- B站的完整下载能力默认关闭，不与独立 B站视频插件争夺链接。

## 许可与署名

本仓库包含上游项目 `astrbot_plugin_parser` 的 MIT 授权代码，因此必须保留其原始版权声明和 MIT 许可证文本。它不表示本仓库归上游作者所有，也不限制本仓库中新增代码的维护者。具体复用文件和上游说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
