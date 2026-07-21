"""AstrBot entry point for querying Wanxiao water and electricity balances."""

from typing import Any, Optional, Tuple

import aiohttp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from wanxiao_client import (
    DEFAULT_TIMEOUT_SECONDS,
    NoBoundRoomsError,
    WanxiaoClient,
    WanxiaoError,
    format_query_report,
)


class WanxiaoElectricityPlugin(Star):
    """Query the configured Wanxiao account without storing room data locally."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[WanxiaoClient] = None
        self._client_config: Optional[Tuple[str, str]] = None

    async def initialize(self):
        """Keep loading successful even before the administrator fills in config."""

    @staticmethod
    def _config_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _get_credentials(self) -> Optional[Tuple[str, str]]:
        school_code = self._config_text(self.config.get("school_code", ""))
        student_account = self._config_text(self.config.get("student_account", ""))
        if not school_code or not student_account:
            return None
        return school_code, student_account

    def _get_client(self, credentials: Tuple[str, str]) -> WanxiaoClient:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
            )
            self._client = None
            self._client_config = None

        if self._client is None or self._client_config != credentials:
            self._client = WanxiaoClient(
                school_code=credentials[0],
                student_account=credentials[1],
                session=self._session,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            )
            self._client_config = credentials
        return self._client

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查水电")
    async def query_water_and_electricity(self, event: AstrMessageEvent):
        """查询当前配置账号绑定房间的水电信息。"""
        credentials = self._get_credentials()
        if credentials is None:
            yield event.plain_result(
                "请先在插件配置中填写 school_code 和 student_account。"
            )
            return

        try:
            results = await self._get_client(credentials).query_bound_rooms()
        except NoBoundRoomsError:
            yield event.plain_result("未找到已绑定的房间。")
            return
        except WanxiaoError:
            yield event.plain_result("水电查询服务暂时不可用，请稍后再试。")
            return

        yield event.plain_result(format_query_report(results))

    async def terminate(self):
        """Close the aiohttp session created and owned by this plugin."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._client = None
        self._client_config = None
