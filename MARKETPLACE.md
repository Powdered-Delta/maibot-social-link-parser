# MaiBot 社媒聚合解析

MaiBot 第三方插件。自动识别社交媒体链接，提供摘要、封面或首图预览，并默认等待链接发送者确认后下载媒体。

仓库：https://github.com/Powdered-Delta/maibot-social-link-parser

当前版本：`0.1.1`

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
- B站解析兼容，但默认关闭以避免与独立 B站插件重复处理
- 解析后 QQ 表情回应、摘要和视频封面或图集首图预览
- 默认确认下载模式：仅原发送者可在同一会话回复“下载”取得完整媒体
- 图集下载失败时保留其他成功图片，并插入缺失图片的文本占位符
- 下载中断时使用 `.part` 与 HTTP Range 重试
- `dry_run` 演练模式：不向平台发送消息，仍完整执行解析和下载链路

## 运行要求

- MaiBot `1.0.0` 至 `1.99.99`
- maibot-plugin-sdk `2.0.0` 至 `2.99.99`
- QQ 实际发送当前使用 OneBot HTTP API；需要配置可访问的 NapCat HTTP API
- B站解析默认关闭；启用前应停用同会话的其他 B站链接解析插件

## 迁移来源

本插件基于 `Zhalslar/astrbot_plugin_parser` 的解析核心进行 MaiBot 适配和持续开发。

- 原仓库：https://github.com/Zhalslar/astrbot_plugin_parser
- 原许可：MIT License
- 适配与新增：MaiBot Hook、确认下载状态机、封面预览、QQ 表情回应、演练模式、下载续传、图集失败占位符等。

## 上架备注

上游来源和许可证说明保留在 README 与 `THIRD_PARTY_NOTICES.md`。本项目没有提交运行时配置、Cookie、缓存、下载媒体或任何密钥。
