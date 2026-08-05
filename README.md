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
- [x] 小红书风控/安全校验页识别：命中时提示配置 Cookie 或代理，而非笼统报解析失败。
- [x] 解析异常原因透传：向用户展示具体原因（如 Cookie 缺失、平台风控、链接失效），底层错误仍完整记录到日志。
- [x] 模块导入加固：解析器内部导入提前到模块加载期完成，规避 MaiBot 插件重载回滚导致的运行时导入失败；命中时提示重启或重载插件。
- [ ] 支持共享 Docker volume 的媒体路径映射，解决 MaiBot 与 NapCat 双容器下的 `file://` 媒体可见性问题。
- [ ] 支持 SOCKS5/SOCKS5h 代理，并为 HTTP/SOCKS 代理增加独立认证字段与凭据 URL 编码，避免将密码直接拼入代理 URL。
- [ ] 建立面向用户的错误分类和提示：区分 Cookie/Token 缺失、非公开或无权限内容、平台风控、链接失效、媒体超限、下载失败与 QQ 投递失败；保留底层错误日志用于诊断（解析路径已透传异常原因，下载与投递路径待补）。
- [ ] 为确认下载状态机补充显式命令，例如 `/链接解析下载 <链接>`，用于多条待下载任务时的无歧义选择。
- [ ] 增加单元测试，覆盖小红书 `.cn` 短链、确认下载权限隔离、Range 续传和图集失败占位符。
- [ ] 接入并修复 X/Twitter：优先使用现有 xdown 路径，补齐 `x.com`、`twitter.com`、`mobile.twitter.com` 识别；yt-dlp 作为可选后备。
- [x] 增加可配置的下载意图正则、群聊/私聊/用户黑白名单，并保持默认确认下载策略。
- [x] 恢复小程序卡片中的真实链接：NapCat 适配器把 B站等小程序卡片的内容链接（`qqdocurl`）从 `raw_message` 移入 `platform_card_payloads`，插件现在会从该负载中补回 URL，避免卡片分享消息不触发解析。

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

默认下载模式为 `confirm`。支持链接的发送者可在同一会话内单发或回复“下载”（也支持“下载 1”这类编号选择）确认下载。普通发言中包含“下载”字样不会触发；如需其他命令，启用高级配置后通过 `download_intent_regex` 显式追加。独立下载命令始终由插件接管：找不到可确认任务时会提示先发送链接或引用详情卡片，不会转交普通聊天。其他用户、其他会话或过期确认不会消费该待下载任务。

发送“链接 + 下载”（任意先后顺序）会在解析成功后直接下载，不必等待概览卡片。回复 bot 的概览卡片并发送“下载”时，插件会按引用卡片中的链接定位待下载内容；引用他人内容不会回退下载自己的最新任务。

### 权限规则

权限分为两个彼此独立的层次，不能混用：

1. **链接发送者全局权限**：`parser_advanced.user_whitelist` 和 `parser_advanced.user_blacklist` 检查新链接的原发送者。发送者通过该检查后，可正常解析链接、创建待下载任务、发送“链接 + 下载”直接下载，以及确认下载自己分享的链接。黑名单优先于白名单；白名单为空时允许所有未被黑名单命中的发送者。代触发者默认也需通过这套全局名单，除非有显式的代触发白名单授权。
2. **聊天范围权限**：群和私聊黑白名单决定插件是否在对应会话工作。它们不用于判定群成员能否代替他人下载。
3. **群聊引用代触发权限**：默认只有原链接发送者能确认下载。其他群成员如需下载他人分享的媒体，必须引用回复 Bot 的对应详情卡片，且由 `parser_advanced.cross_sender_download_access_mode` 授权：`disabled` 始终仅原发送者，`whitelist` 仅群或用户白名单命中时允许，`blacklist` 则除群或用户黑名单命中外均允许。代触发者也默认受链接发送者全局黑白名单约束；若其未通过全局名单，只有被显式加入代触发群白名单或用户白名单时才可例外代触发。

即使代触发权限命中，也必须在群聊中引用 Bot 的详情卡片，并由被引用卡片中的链接唯一命中待下载内容。引用普通成员消息、只发送“下载”、或引用不含唯一链接的卡片都不会代为下载。

同一详情卡片的媒体在发送成功后会标记为已发送；后续回复不会重复下载。下载进行中也会拒绝重复请求。若下载或发送失败，任务恢复为可确认状态，可再次回复“下载”重试。

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

### 运行依赖：需要 ffmpeg

媒体下载依赖 MaiBot Runner（容器）中存在 `ffmpeg`。抖音/小红书/小黑盒经 yt-dlp 通常直接拿到单文件 mp4，不触发 ffmpeg；但 **B站视频是 DASH 分离流，合并音视频必然调用 `ffmpeg`**，源端只提供分离流或非 mp4 格式时同样需要。

MaiBot 官方镜像**不保证内置 ffmpeg**：其 Dockerfile 未安装 ffmpeg，Playwright `install-deps chromium` 的依赖列表也不含 ffmpeg；`sengokucola/maibot:latest` 与基础镜像 `python:3.13-slim` 都是浮动标签，重建镜像后容器里的 ffmpeg 可能来去变化。同一套部署在镜像/容器重建后开始报“媒体需要 FFmpeg 合并音视频”通常就是这个原因，与插件版本无关。

确认容器内是否有 ffmpeg：`docker exec <core容器名> which ffmpeg`。

**临时应急**（仅当前容器有效，重建即丢失）：直接进入运行中的容器安装：

```bash
docker exec -it <core容器名> bash -c "apt-get update && apt-get install -y --no-install-recommends ffmpeg"
```

装完立刻生效，可先恢复 B站下载；但容器一旦重建该安装就会消失，仍需按下面的方案做永久修复。

**永久修复**是**在 MaiBot 使用的 Docker 镜像中预装 ffmpeg 并重建容器**，不要在运行中的容器内临时安装（重建即丢失）。可基于官方镜像做派生镜像：

```dockerfile
FROM sengokucola/maibot:latest
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
```

```bash
docker build -t maibot-ffmpeg .
```

compose 中把 `core.image` 指向 `maibot-ffmpeg` 后重建即可。

## 配置

`config.example.toml` 提供了配置参考。常用字段如下：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `plugin.enabled` | `true` | 是否启用插件 |
| `plugin.dry_run` | `false` | 演练模式，不实际发送 QQ 消息 |
| `parser.enabled_platforms` | 抖音、小红书、小黑盒 | WebUI 多选平台开关；B站默认关闭，需加入 `bilibili` 才会解析 `b23.tv` 和 B站小程序卡片 |
| `parser.block_ai_reply` | `true` | 命中链接后是否中止 MaiBot 普通聊天流程 |
| `parser.download_mode` | `confirm` | `confirm` 为确认下载，`automatic` 为自动下载 |
| `parser.send_preview_image` | `true` | 概览是否发送视频封面或图集首图 |
| `parser_advanced.enabled` | `false` | 高级解析开关；关闭时忽略本分组全部字段并使用内置默认值 |
| `parser_advanced.gallery_delivery_mode` | `default` | 静态图集默认跟随 QQ 通道；WebSocket 经 MaiBot 合并转发发送，可设为 `http_forward` 使用 OneBot HTTP |
| `parser_advanced.pending_download_ttl_seconds` | `600` | 等待用户确认下载的有效期，单位秒 |
| `parser_advanced.pending_download_selection` | `latest` | 回复 bot 概览卡片时优先按引用链接精确选择；非回复的“下载”在 `latest` 下选最新一条，`number` 要求“下载 1”等明确编号 |
| `parser_advanced.cross_sender_download_group_whitelist` | `[]` | 这些群内允许成员引用 Bot 详情卡片代触发下载；也可显式豁免请求者的全局用户黑白名单 |
| `parser_advanced.cross_sender_download_user_whitelist` | `[]` | 这些用户可引用 Bot 详情卡片代触发下载；也可显式豁免其全局用户黑白名单 |
| `parser_advanced.cross_sender_download_access_mode` | `whitelist` | 跨用户下载授权模式：`disabled`、`whitelist` 或 `blacklist` |
| `parser_advanced.cross_sender_download_group_blacklist` | `[]` | 黑名单模式下，禁止跨用户下载的群 |
| `parser_advanced.cross_sender_download_user_blacklist` | `[]` | 黑名单模式下，禁止跨用户下载的用户 |
| `parser_advanced.reaction_emoji_enabled` | `true` | 识别链接后是否添加 QQ 表情回应 |
| `parser_advanced.group_whitelist` | `[]` | 仅这些群自动解析；留空表示所有群 |
| `parser_advanced.group_blacklist` | `[]` | 这些群不自动解析；与白名单重叠时黑名单优先 |
| `parser_advanced.user_whitelist` | `[]` | 链接发送者全局白名单：仅这些用户分享的链接可解析、创建待下载任务或直接下载；代触发者默认也受限，除非被显式加入代触发白名单 |
| `parser_advanced.user_blacklist` | `[]` | 链接发送者全局黑名单：这些用户分享的链接不会解析、创建待下载任务或直接下载；代触发者默认也受限，显式代触发白名单可例外；黑名单优先 |
| `parser_advanced.private_whitelist` | `[]` | 仅这些用户的私聊链接会解析；留空表示允许所有私聊 |
| `parser_advanced.private_blacklist` | `[]` | 这些用户的私聊链接不解析 |
| `parser_advanced.download_intent_regex` | `""` | 追加的独立下载确认命令正则，与默认“下载”取并集，同时支持“下载 1”编号选择；留空仅用默认 |
| `parser_advanced.debounce_seconds` | `120` | 同一会话内重复链接的忽略时长，单位秒；`0` 禁用去重 |
| `parser_advanced.max_images` | `9` | 单条链接最多发送的图片数 |
| `parser_advanced.max_text_chars` | `700` | 摘要正文最大字符数 |
| `parser_advanced.send_images` | `true` | 是否发送图集与图片媒体 |
| `parser_advanced.send_video` | `true` | 是否在确认下载后回传视频 |
| `parser_advanced.use_forward_for_multi` | `true` | 静态图集和图文是否使用合并转发 |
| `parser_advanced.reaction_emoji_id` | `76` | QQ 表情回应 ID |
| `parser_advanced.source_max_size_mb` | `80` | 单个媒体最大下载大小，单位 MB |
| `parser_advanced.source_max_minutes` | `8` | 视频最大时长，单位分钟 |
| `network.proxy` | `""` | 解析/下载代理 |
| `network_advanced.enabled` | `false` | 高级网络开关；关闭时忽略本分组全部字段并使用内置默认值 |
| `network_advanced.use_proxy_platforms` | `[]` | 仅选中的平台通过上方代理访问 |
| `network_advanced.common_timeout` | `30` | 解析请求超时，单位秒 |
| `network_advanced.download_timeout` | `120` | 媒体下载超时，单位秒 |
| `network_advanced.download_retry_times` | `3` | 媒体下载重试次数 |
| `delivery.delivery_mode` | `websocket` | 默认经 MaiBot 的 NapCat WebSocket 网关发送；可改为 `http` 使用兼容直连 |
| `api.host`、`api.port`、`api.token` | - | 仅 `delivery.delivery_mode = "http"` 时使用的 NapCat OneBot HTTP 配置 |

默认通过 MaiBot 已启用的 NapCat WebSocket 网关发送，配置页的“HTTP 兼容后备”分组无需填写。静态图集或图文会经 `send.forward` 作为合并转发发送，图片以 Base64 传给 Host，不依赖 NapCat 读取 MaiBot 的本地媒体路径。选择 HTTP 兼容模式时，才需要让 `api.host`、`api.port`、`api.token` 指向 MaiBot 容器可访问的 NapCat OneBot HTTP 服务。

支持 `send.hybrid` 的 MaiBot Host 会将标题、作者、正文、链接、预览图和“回复“下载”获取原媒体”组合成一条普通 QQ 图文消息，不使用合并转发。发送失败时会自动退回为原有的文字和预览图两条消息。

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

- 默认 QQ 发送通过 MaiBot 的 NapCat WebSocket 网关；静态图集会使用合并转发，HTTP 是可选兼容模式。含视频或 Live Photo 的混合图集保持按原始顺序逐条发送。
- 图集发送保留下载到的源文件；MaiBot 聊天记录整合只消费摘要和元数据，不参与原图再编码。
- NapCat 与 MaiBot 分属不同容器时，`file://` 媒体路径必须对 NapCat 可见。生产环境需配置共享 volume 或保留现有可用的媒体路径方案。
- B站的完整下载能力默认关闭，不与独立 B站视频插件争夺链接。
- B站小程序卡片依赖 NapCat 适配器透传 `message_info.additional_config.platform_card_payloads`。本插件会从其中的 `qqdocurl` 恢复真实链接；若日志仍未出现 B站链接候选，请先确认适配器版本和该字段是否存在。
- 回复或 @ 当前 bot 的消息交给 MaiBot 正常处理（联网搜索等），仅显式下载意图仍由本插件接管；避免对详情卡片的回复再次触发链接解析。
- 引用回复 bot 时 QQ 会自动附带 `@bot 群名片`，并置位 `is_mentioned`。插件以 `is_mentioned` 为标准判定消息是否指向 bot，并在命令识别前剔除消息中的 `[reply]` 占位、`@群名片` 占位文本，以及按 reply 段 `target_message_content` 原文剔除内联进纯文本的整块引用预览（含链接与“回复‘下载’获取原媒体”提示），避免它们破坏独立的“下载”命令匹配或被误判为新一轮直接下载请求。

## 更新历史

- `0.3.7`：修复引用 bot 概览卡片后回复“下载”未被接管的问题。MaiBot 会把 reply 组件的 `target_message_content` 整块内联进 `processed_plain_text`，导致概览内的链接被 `direct_download` 误判。现按引用原文预览精确剔除，引用回复“下载”恢复为对既有待下载任务的确认。
- `0.3.6`：以 `is_mentioned` 为标准判定 bot 寻址；命令识别前剔除引用回复自动附带的 `[reply]` 与 `@bot 群名片` 占位文本，使引用回复“下载”在 QQ 自动 @ 群名片时仍被准确接管。
- `0.3.5`：明确并实现权限边界：全局用户黑白名单限制链接原发送者，也默认限制代触发者；群聊中他人下载必须引用 Bot 详情卡片，只有显式代触发白名单可豁免请求者的全局用户限制。
  - `0.3.4`：仅独立的“下载”或“下载 序号”命令会确认下载，普通发言或回复内容中包含“下载”不会触发；任意回复消息不再作为新的链接分享重新解析。跨用户下载授权支持禁用、白名单和黑名单模式；详情卡片的媒体发送成功后标记为已发送，下载中或已发送时拒绝重复请求，失败后允许再次确认；统一媒体下载额外重试次数默认值为 3。
- `0.3.2`：支持“链接 + 下载”直接下载；回复详情卡片时按引用链接精确选择待下载任务，避免跨用户误下载最新内容；可通过跨用户下载群/用户白名单授权其他人下载；补充 B站小程序卡片的顶层负载兼容与未启用 B站解析器的诊断日志。
- `0.3.0`：修复 B站等小程序卡片分享不触发解析（从 `platform_card_payloads` 补回 `qqdocurl` 真实链接）；配置拆分出 `parser_advanced` 与 `network_advanced` 两个独立高级分组，每分组带独立开关，关闭时忽略该组全部字段并使用内置默认值；新增入口检查单元测试。
- `0.2.0`：增加可配置的下载意图正则、群聊/私聊/用户黑白名单。
- `0.1.x`：确认下载状态机、封面预览、QQ 表情回应、演练模式、下载续传、图集失败占位符等。

## 许可与署名

本仓库包含上游项目 `astrbot_plugin_parser` 的 MIT 授权代码，因此必须保留其原始版权声明和 MIT 许可证文本。它不表示本仓库归上游作者所有，也不限制本仓库中新增代码的维护者。具体复用文件和上游说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
