class ParseException(Exception):
    """异常基类"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TipException(ParseException):
    """提示异常"""

    pass


class DownloadException(ParseException):
    """下载异常"""

    def __init__(self, message: str | None = None):
        super().__init__(message or "媒体下载失败")


class LinkExpiredException(DownloadException):
    """媒体直链被平台拒绝（403/401/410）。

    抖音等平台的 CDN 直链带防盗链签名：即使 ``x-expires`` 未到期，同一签名 URL
    重复请求也会被拒。这类失败重试同一 URL 只是空转，必须重新解析原分享链接以
    换取新直链。
    """

    def __init__(self, message: str | None = None):
        super().__init__(message or "媒体直链已失效，需要重新解析")


class DownloadLimitException(DownloadException):
    """下载超过限制异常"""

    pass


class SizeLimitException(DownloadLimitException):
    """下载大小超过限制异常"""

    def __init__(self):
        super().__init__("媒体大小超过配置限制，取消下载")


class DurationLimitException(DownloadLimitException):
    """下载时长超过限制异常"""

    def __init__(self):
        super().__init__("媒体时长超过配置限制，取消下载")


class ZeroSizeException(DownloadException):
    """下载大小为 0 异常"""

    def __init__(self):
        super().__init__("媒体大小为 0, 取消下载")


class RedirectException(DownloadException):
    """下载重定向异常"""

    def __init__(self):
        super().__init__("媒体链接重定向时出现异常")
