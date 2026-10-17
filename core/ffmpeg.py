import asyncio
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Final

DOWNLOAD_RETRY_TIMES: Final[int] = 3

from astrbot.api import logger

FFMPEG_DIR_NAME: Final[str] = "ffmpeg"

# BtbN FFmpeg-Builds 的稳定 latest 资产名（GPL 构建，含 libx264）。master 分支持续发布，
# release/latest/download 始终重定向到最新版本。可通过配置项覆盖 download_url。
DEFAULT_FFMPEG_URLS: Final[dict[str, str]] = {
    "linux64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
        "ffmpeg-master-latest-linux64-gpl.tar.xz"
    ),
    "linuxarm64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
        "ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
    ),
    "win64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
        "ffmpeg-master-latest-win64-gpl.zip"
    ),
    "winarm64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
        "ffmpeg-master-latest-winarm64-gpl.zip"
    ),
}

_configured_ffmpeg: Path | None = None


def ffmpeg_binary_name() -> str:
    """平台对应的可执行文件名。"""
    return "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def detect_platform() -> str | None:
    """返回支持的下载平台 key，否则 None。"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "linux64"
        if machine in ("aarch64", "arm64"):
            return "linuxarm64"
        return None
    if system == "windows":
        if machine in ("aarch64", "arm64"):
            return "winarm64"
        return "win64"
    return None


def default_download_url(platform_key: str | None = None) -> str | None:
    return DEFAULT_FFMPEG_URLS.get(platform_key or detect_platform() or "")


def local_binary(data_dir: Path) -> Path | None:
    """数据目录下插件自举的 ffmpeg 可执行文件。"""
    path = data_dir / FFMPEG_DIR_NAME / ffmpeg_binary_name()
    return path if path.is_file() else None


def resolve_ffmpeg_path(data_dir: Path) -> Path | None:
    """优先返回插件数据目录下的自举 ffmpeg，其次回退到系统 PATH。"""
    local = local_binary(data_dir)
    if local:
        return local
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def set_configured_ffmpeg(path: Path | None) -> None:
    global _configured_ffmpeg
    _configured_ffmpeg = path


def configured_ffmpeg() -> Path | None:
    cfg = _configured_ffmpeg
    if cfg and cfg.is_file():
        return cfg
    return None


def executable() -> str:
    """给子进程使用的 ffmpeg 路径；未配置时回退为 PATH 查找的 ``ffmpeg``。"""
    cfg = configured_ffmpeg()
    return str(cfg) if cfg else "ffmpeg"


async def ensure_ffmpeg(
    data_dir: Path,
    *,
    enabled: bool = True,
    download_url: str = "",
) -> Path | None:
    """确保存在可用的 ffmpeg，必要时自动下载静态构建到数据目录。

    返回可用的 ffmpeg 路径；失败或平台不支持时仅记录告警并返回 None，
    不会阻断插件启动。已有本地二进制时直接复用。
    """
    if not enabled:
        existing = local_binary(data_dir)
        if existing:
            set_configured_ffmpeg(existing)
        return existing

    existing = local_binary(data_dir)
    if existing:
        set_configured_ffmpeg(existing)
        return existing

    url = (download_url or "").strip() or default_download_url()
    if not url:
        logger.warning(
            "ffmpeg 自举: 不支持的平台 %s/%s，请手动提供 ffmpeg",
            platform.system(),
            platform.machine(),
        )
        return None

    try:
        binary = await asyncio.to_thread(_download_and_install, data_dir, url)
    except Exception as exc:
        logger.warning("ffmpeg 自举失败: %s", exc)
        return None

    if binary:
        set_configured_ffmpeg(binary)
    else:
        logger.warning("ffmpeg 自举: 下载或解压后未找到可执行文件")
    return binary


def _download_and_install(data_dir: Path, url: str) -> Path | None:
    data_dir.mkdir(parents=True, exist_ok=True)
    target_dir = data_dir / FFMPEG_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    for stale in data_dir.glob(".ffmpeg-dl-*"):
        shutil.rmtree(stale, ignore_errors=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".ffmpeg-dl-", dir=data_dir))
    try:
        archive_path = tmp_dir / "ffmpeg-archive"
        _download_to(url, archive_path)
        return _extract_binary(archive_path, target_dir, ffmpeg_binary_name())
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download_to(url: str, dest: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "maibot-social-link-parser/ffmpeg-bootstrap"}
    )
    last_error: Exception | None = None
    for attempt in range(DOWNLOAD_RETRY_TIMES):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with dest.open("wb") as file:
                    shutil.copyfileobj(response, file)
            return
        except (urllib.error.URLError, OSError) as exc:
            # A truncated download leaves a partial file; remove it before retrying
            # so the next attempt does not append to corrupt bytes.
            last_error = exc
            dest.unlink(missing_ok=True)
            if attempt + 1 < DOWNLOAD_RETRY_TIMES:
                logger.warning(
                    "ffmpeg 自举下载中断，重试 (%s/%s): %s",
                    attempt + 1,
                    DOWNLOAD_RETRY_TIMES,
                    exc,
                )
    raise last_error if last_error is not None else RuntimeError("ffmpeg 下载失败")


def _extract_binary(archive_path: Path, target_dir: Path, binary_name: str) -> Path | None:
    """从 tar/zip 归档中取出 ``bin/<binary_name>`` 到 target_dir，返回目标路径。

    仅提取需要的成员，避免解包整个归档；路径分隔符统一为 ``/`` 判断。
    """
    target = target_dir / binary_name
    extracted = False
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                if (
                    member.isfile()
                    and "/bin/" in member.name
                    and Path(member.name.replace("\\", "/")).name == binary_name
                ):
                    source = tf.extractfile(member)
                    if source is None:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("wb") as file:
                        shutil.copyfileobj(source, file)
                    extracted = True
                    break
    elif zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if (
                    not info.is_dir()
                    and "/bin/" in name
                    and Path(name).name == binary_name
                ):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as source:
                        with target.open("wb") as file:
                            shutil.copyfileobj(source, file)
                    extracted = True
                    break
    if extracted:
        _make_executable(target)
        return target
    return None


def _make_executable(path: Path) -> None:
    if os.name != "nt":
        try:
            path.chmod(0o755)
        except OSError:
            logger.warning("ffmpeg 自举: 无法设置可执行权限 %s", path)
