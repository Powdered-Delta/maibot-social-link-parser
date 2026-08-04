# MaiBot 社媒聚合解析

MaiBot 第三方插件。自动识别社交媒体链接，提供摘要、封面或首图预览，并默认等待链接发送者确认后下载媒体。

仓库：https://github.com/Powdered-Delta/maibot-social-link-parser

当前版本：`0.3.4`

## 推荐分类

- Utility Tools
- External Integration

## 推荐标签

- link-parser
- douyin
- xhs
- xiaoheihe
- bilibili
- napcat
- social-media

## 当前功能

- 抖音短链、视频、图集解析
- 小红书短链、图文、视频解析，包含 `http://xhslink.cn` 分享链接
- 小黑盒帖子和游戏分享解析
- B站视频、`b23.tv` 短链与小程序卡片解析兼容，但默认关闭以避免与独立 B站插件重复处理
- 解析后 QQ 表情回应、摘要和视频封面或图集首图预览
- 图文混编概览：将文字、预览图和下载提示合为普通 QQ 消息
- 静态图集通过 MaiBot WebSocket 网关使用合并转发发送
- 默认确认下载模式：仅原发送者可在同一会话单发或回复“下载”取得完整媒体；普通发言中的“下载”不触发
- 发送“链接 + 下载”可在解析后直接下载；回复详情卡片时按引用链接精确选择，跨用户下载可选禁用、白名单或黑名单授权，成功发送后拒绝重复下载
- 图集下载失败时保留其他成功图片，并插入缺失图片的文本占位符
- 下载中断时使用 `.part` 与 HTTP Range 重试
- `dry_run` 演练模式：不向平台发送消息，仍完整执行解析和下载链路
- 小红书风控/安全校验页识别，并提示配置 Cookie 或代理
- 解析异常原因透传：向用户展示具体原因，底层错误保留在日志
- 解析器内部导入提前到模块加载期，规避 MaiBot 插件重载回滚导致的运行时导入失败
- 可配置的下载确认意图正则，以及群聊/私聊/用户黑白名单，保持默认确认下载策略
- 恢复 B站等小程序卡片中的真实链接（`qqdocurl`），避免卡片分享不触发解析
- 配置拆分为解析与网络两个独立高级分组，各组可独立开关并内置默认值

## 运行要求

- MaiBot `1.0.0` 至 `1.99.99`
- maibot-plugin-sdk `2.0.0` 至 `2.99.99`
- 默认通过 MaiBot 的 NapCat WebSocket 网关发送；可选 OneBot HTTP 兼容模式
- B站解析默认关闭；启用前应停用同会话的其他 B站链接解析插件

## 迁移来源

本插件基于 `Zhalslar/astrbot_plugin_parser` 的解析核心进行 MaiBot 适配和持续开发。

- 原仓库：https://github.com/Zhalslar/astrbot_plugin_parser
- 原许可：MIT License
- 适配与新增：MaiBot Hook、确认下载状态机、封面预览、QQ 表情回应、演练模式、下载续传、图集失败占位符等。

## 上架备注

上游来源和许可证说明保留在 README 与 `THIRD_PARTY_NOTICES.md`。本项目没有提交运行时配置、Cookie、缓存、下载媒体或任何密钥。
