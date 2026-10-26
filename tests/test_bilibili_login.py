# -*- coding: utf-8 -*-
"""B站 Cookie 校验开关（``cookies.bilibili_check_valid``）行为测试。

该开关直接决定是否命中 B站登录态接口 ``Credential.check_valid``。这类退化是
静默的：关掉后不再有「凭证已过期」告警，只是拿不到高画质。所以两头都要钉住——
开启时必须真的去校验，关闭时一次都不许发请求。

需要 bilibili-api 才能导入 ``login`` 模块；缺依赖时整体跳过。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("bilibili_api")

from maibot_social_link_parser.core.config import PluginConfig  # noqa: E402
from maibot_social_link_parser.core.parsers.bilibili import login as bili_login  # noqa: E402


RAW_COOKIES = "SESSDATA=abc123; bili_jct=def456"


class _FakeCredential:
    """最小 Credential 替身：只记录被调用的探测方法。"""

    def __init__(self) -> None:
        self.cookies = RAW_COOKIES
        self.check_valid_calls = 0
        self.check_refresh_calls = 0
        self.refresh_calls = 0
        self.valid = True
        self.needs_refresh = False

    async def check_valid(self) -> bool:
        self.check_valid_calls += 1
        return self.valid

    async def check_refresh(self) -> bool:
        self.check_refresh_calls += 1
        return self.needs_refresh

    async def refresh(self) -> None:
        self.refresh_calls += 1

    def has_ac_time_value(self) -> bool:
        return True

    def has_bili_jct(self) -> bool:
        return True

    def get_cookies(self) -> dict[str, str]:
        return {"SESSDATA": "abc123", "bili_jct": "def456"}


@pytest.fixture()
def fake_credential(monkeypatch: pytest.MonkeyPatch):
    """把 ``Credential.from_cookies`` 换成替身，并返回按构造顺序收集的实例列表。"""

    created: list[_FakeCredential] = []

    def _from_cookies(cookies: object) -> _FakeCredential:
        instance = _FakeCredential()
        created.append(instance)
        return instance

    monkeypatch.setattr(
        bili_login.Credential, "from_cookies", staticmethod(_from_cookies)
    )
    return created


def make_login(data_dir: Path, *, check_valid_enabled: bool) -> bili_login.BilibiliLogin:
    config = PluginConfig(
        data_dir=data_dir,
        enabled_platforms=["bilibili"],
        bilibili_cookies=RAW_COOKIES,
        bilibili_check_cookie_valid=check_valid_enabled,
    )
    return bili_login.BilibiliLogin(config)


class TestCheckValidSwitchWiring:
    def test_core_config_defaults_to_validating(self, tmp_path: Path) -> None:
        """默认必须校验：老配置缺字段时不能被静默降级成「不校验」。"""

        config = PluginConfig(data_dir=tmp_path, enabled_platforms=["bilibili"])
        assert config.parser.bilibili.check_cookie_valid is True

    def test_switch_reaches_bilibili_login(self, tmp_path: Path) -> None:
        enabled = make_login(tmp_path / "a", check_valid_enabled=True)
        disabled = make_login(tmp_path / "b", check_valid_enabled=False)

        assert enabled.check_valid_enabled is True
        assert disabled.check_valid_enabled is False


class TestCheckValidEnabled:
    def test_probes_login_state(self, tmp_path: Path, fake_credential: list[_FakeCredential]) -> None:
        login = make_login(tmp_path, check_valid_enabled=True)

        credential = asyncio.run(_await_credential(login))
        assert credential is not None
        assert fake_credential[0].check_valid_calls == 1

    def test_expired_cookie_falls_back_and_reports_none(
        self, tmp_path: Path, fake_credential: list[_FakeCredential]
    ) -> None:
        login = make_login(tmp_path, check_valid_enabled=True)
        login._credential = _FakeCredential()
        login._credential.valid = False

        assert asyncio.run(_await_credential(login)) is None
        assert login._credential.check_valid_calls == 1


class TestCheckValidDisabled:
    def test_never_probes_login_state(
        self, tmp_path: Path, fake_credential: list[_FakeCredential]
    ) -> None:
        """关闭后一次 ``check_valid`` / ``check_refresh`` 都不许发。"""

        login = make_login(tmp_path, check_valid_enabled=False)

        credential = asyncio.run(_await_credential(login))
        assert credential is not None
        assert credential is fake_credential[0]
        assert credential.check_valid_calls == 0
        assert credential.check_refresh_calls == 0

    def test_expired_cookie_still_used(
        self, tmp_path: Path, fake_credential: list[_FakeCredential]
    ) -> None:
        """Cookie 已失效也要照用——用户显式要求信任配置值。"""

        login = make_login(tmp_path, check_valid_enabled=False)
        login._credential = _FakeCredential()
        login._credential.valid = False

        credential = asyncio.run(_await_credential(login))
        assert credential is login._credential
        assert login._credential.check_valid_calls == 0

    def test_no_refresh_attempt(
        self, tmp_path: Path, fake_credential: list[_FakeCredential]
    ) -> None:
        login = make_login(tmp_path, check_valid_enabled=False)
        login._credential = _FakeCredential()
        login._credential.needs_refresh = True

        asyncio.run(_await_credential(login))
        assert login._credential.check_refresh_calls == 0
        assert login._credential.refresh_calls == 0


async def _await_credential(login: bili_login.BilibiliLogin):
    return await login.credential
