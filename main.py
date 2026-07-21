"""AstrBot entry point for querying Wanxiao water and electricity balances."""

import asyncio
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

if __package__:
    from .wanxiao_client import (
        DEFAULT_TIMEOUT_SECONDS,
        NoBoundRoomsError,
        WanxiaoClient,
        WanxiaoError,
        format_query_report,
    )
else:
    from wanxiao_client import (
        DEFAULT_TIMEOUT_SECONDS,
        NoBoundRoomsError,
        WanxiaoClient,
        WanxiaoError,
        format_query_report,
    )

MAX_ACCOUNT_COUNT = 16
MAX_ACCOUNT_FIELD_LENGTH = 64
MAX_ACCOUNT_NAME_LENGTH = 32
MAX_REPORT_CHARS = 3500


class AccountConfigurationError(ValueError):
    """A configuration error whose message is safe to return to an administrator."""


@dataclass(frozen=True, repr=False)
class AccountConfig:
    """One validated Wanxiao account, kept out of logs and error representations."""

    name: str
    school_code: str
    student_account: str

    @property
    def credentials(self) -> Tuple[str, str]:
        return self.school_code, self.student_account

    def __repr__(self) -> str:
        return "AccountConfig(name_set={}, student_account={!r})".format(
            bool(self.name), mask_student_account(self.student_account)
        )


def mask_student_account(student_account: str) -> str:
    """Return a stable, non-sensitive account suffix for user-visible labels."""
    if len(student_account) <= 4:
        return "*" * len(student_account)
    return "****{}".format(student_account[-4:])


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


class WanxiaoElectricityPlugin(Star):
    """Query configured Wanxiao accounts without storing room data locally."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._clients: Dict[Tuple[str, str], WanxiaoClient] = {}
        self._account_semaphore = asyncio.Semaphore(2)
        self._lifecycle_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._terminated = False

    async def initialize(self):
        """Keep loading successful even before the administrator fills in config."""

    @staticmethod
    def _parse_account_name(value: Any) -> str:
        if not isinstance(value, str):
            raise AccountConfigurationError("账号配置不正确，请检查 accounts 配置。")
        value = value.strip()
        if len(value) > MAX_ACCOUNT_NAME_LENGTH or _contains_control(value):
            raise AccountConfigurationError("账号配置不正确，请检查 accounts 配置。")
        return value

    @staticmethod
    def _parse_account_field(value: Any) -> str:
        if not isinstance(value, str):
            raise AccountConfigurationError("账号配置不正确，请检查 accounts 配置。")
        value = value.strip()
        if not 1 <= len(value) <= MAX_ACCOUNT_FIELD_LENGTH:
            raise AccountConfigurationError("账号配置不正确，请检查 accounts 配置。")
        if any(character.isspace() for character in value) or _contains_control(value):
            raise AccountConfigurationError("账号配置不正确，请检查 accounts 配置。")
        return value

    def _get_accounts(self) -> List[AccountConfig]:
        configured_accounts = self.config.get("accounts", [])
        if not isinstance(configured_accounts, list):
            raise AccountConfigurationError("账号配置不正确，请检查 accounts 配置。")
        if not configured_accounts:
            return []
        if len(configured_accounts) > MAX_ACCOUNT_COUNT:
            raise AccountConfigurationError("账号最多只能配置 16 个条目。")

        accounts: List[AccountConfig] = []
        seen_credentials: Set[Tuple[str, str]] = set()
        for entry in configured_accounts:
            if not isinstance(entry, Mapping):
                raise AccountConfigurationError(
                    "账号配置不正确，请检查 accounts 配置。"
                )

            enabled = entry.get("enabled", True)
            if not isinstance(enabled, bool):
                raise AccountConfigurationError(
                    "账号配置不正确，请检查 accounts 配置。"
                )
            if not enabled:
                continue

            account = AccountConfig(
                name=self._parse_account_name(entry.get("name", "")),
                school_code=self._parse_account_field(entry.get("school_code", "")),
                student_account=self._parse_account_field(
                    entry.get("student_account", "")
                ),
            )
            if account.credentials in seen_credentials:
                continue
            seen_credentials.add(account.credentials)
            accounts.append(account)

        if not accounts:
            raise AccountConfigurationError(
                "未找到已启用的有效账号，请检查 accounts 配置。"
            )
        return accounts

    def _get_client(self, credentials: Tuple[str, str]) -> WanxiaoClient:
        if self._terminated:
            raise RuntimeError("插件已停止")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
            )
            self._clients = {}

        client = self._clients.get(credentials)
        if client is None:
            client = WanxiaoClient(
                school_code=credentials[0],
                student_account=credentials[1],
                session=self._session,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            )
            self._clients[credentials] = client
        return client

    async def _close_clients(self, clients: Sequence[WanxiaoClient]) -> None:
        if not clients:
            return

        results = await asyncio.gather(
            *(client.close() for client in clients),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _cleanup_resources(
        self,
        clients: Sequence[WanxiaoClient],
        session: Optional[aiohttp.ClientSession],
    ) -> None:
        try:
            await self._close_clients(clients)
        finally:
            if session is not None and not session.closed:
                await session.close()

    def _clear_cleanup_task(self, cleanup_task: asyncio.Task) -> None:
        if self._cleanup_task is cleanup_task:
            self._cleanup_task = None

    @staticmethod
    async def _wait_for_cleanup(cleanup_task: asyncio.Task) -> None:
        cancellation: Optional[asyncio.CancelledError] = None
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as error:
                current_task = asyncio.current_task()
                if cleanup_task.done() and (
                    current_task is None or current_task.cancelling() == 0
                ):
                    break
                if cancellation is None:
                    cancellation = error
                if current_task is not None and hasattr(current_task, "uncancel"):
                    current_task.uncancel()
            except Exception:
                if cleanup_task.done():
                    break
                raise

        try:
            cleanup_task.result()
        except BaseException as error:
            if cancellation is not None:
                raise cancellation from error
            raise
        if cancellation is not None:
            raise cancellation

    async def _sync_clients(
        self, accounts: Sequence[AccountConfig]
    ) -> List[WanxiaoClient]:
        active_credentials = {account.credentials for account in accounts}
        inactive_clients = [
            self._clients.pop(credentials)
            for credentials in tuple(self._clients)
            if credentials not in active_credentials
        ]
        await self._close_clients(inactive_clients)
        return [self._get_client(account.credentials) for account in accounts]

    @staticmethod
    async def _cancel_and_wait(tasks: Sequence[asyncio.Task]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _account_label(account: AccountConfig) -> str:
        masked_account = mask_student_account(account.student_account)
        if account.name:
            return "{}（学号 {}）".format(account.name, masked_account)
        return "账号 {}".format(masked_account)

    @classmethod
    def _account_title(cls, result_index: int, account: AccountConfig) -> str:
        return "【{}. {}】".format(result_index, cls._account_label(account))

    @staticmethod
    def _split_report_body(report: str, body_limit: int) -> List[str]:
        chunks: List[str] = []
        current: List[str] = []
        current_length = 0

        for line in report.splitlines(keepends=True):
            if len(line) > body_limit:
                if current:
                    chunks.append("".join(current))
                    current = []
                    current_length = 0
                for start in range(0, len(line), body_limit):
                    chunks.append(line[start : start + body_limit])
                continue

            if current and current_length + len(line) > body_limit:
                chunks.append("".join(current))
                current = []
                current_length = 0
            current.append(line)
            current_length += len(line)

        if current:
            chunks.append("".join(current))
        return chunks or [""]

    @classmethod
    def _split_account_report(
        cls,
        account: AccountConfig,
        report: str,
        result_index: int = 1,
    ) -> List[str]:
        title = cls._account_title(result_index, account)
        complete_report = "{}\n{}".format(title, report)
        if len(complete_report) <= MAX_REPORT_CHARS:
            return [complete_report]

        max_chunk_digits = len(str(max(1, len(report))))
        placeholder = "9" * max_chunk_digits
        prefix_budget = len("{} ({}/{})\n".format(title, placeholder, placeholder))
        body_limit = max(1, MAX_REPORT_CHARS - prefix_budget)
        chunks = cls._split_report_body(report, body_limit)
        chunk_count = len(chunks)
        return [
            "{} ({}/{})\n{}".format(title, index, chunk_count, chunk)
            for index, chunk in enumerate(chunks, start=1)
        ]

    async def _query_account(
        self,
        account: AccountConfig,
        client: WanxiaoClient,
    ) -> Tuple[AccountConfig, str]:
        async with self._account_semaphore:
            try:
                results = await client.query_bound_rooms()
            except NoBoundRoomsError:
                return account, "未找到已绑定的房间。"
            except WanxiaoError:
                return account, "水电查询服务暂时不可用，请稍后再试。"
        return account, format_query_report(results)

    async def _build_query_messages(self) -> List[str]:
        if self._terminated:
            return ["插件已停止，无法查询水电。"]

        try:
            accounts = self._get_accounts()
        except AccountConfigurationError as error:
            await self._sync_clients(())
            return [str(error)]

        if not accounts:
            await self._sync_clients(())
            return ["请先在插件配置中添加并启用账号。"]

        clients = await self._sync_clients(accounts)
        tasks: List[asyncio.Task] = []
        try:
            for account, client in zip(accounts, clients):
                tasks.append(asyncio.create_task(self._query_account(account, client)))
            reports = await asyncio.gather(*tasks)
        except BaseException:
            await self._cancel_and_wait(tasks)
            raise

        messages: List[str] = []
        for result_index, (account, report) in enumerate(reports, start=1):
            messages.extend(
                self._split_account_report(
                    account,
                    report,
                    result_index=result_index,
                )
            )
        return messages

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查水电")
    async def query_water_and_electricity(self, event: AstrMessageEvent):
        """查询所有已启用账号绑定房间的水电信息。"""
        async with self._lifecycle_lock:
            messages = await self._build_query_messages()

        for message in messages:
            yield event.plain_result(message)

    async def terminate(self):
        """Wait for active queries, then close owned resources exactly once."""
        async with self._lifecycle_lock:
            cleanup_task = self._cleanup_task
            if cleanup_task is None:
                if self._terminated:
                    return

                self._terminated = True
                clients = list(self._clients.values())
                session = self._session
                self._clients = {}
                self._session = None
                cleanup_task = asyncio.create_task(
                    self._cleanup_resources(clients, session)
                )
                self._cleanup_task = cleanup_task
                cleanup_task.add_done_callback(self._clear_cleanup_task)

        await self._wait_for_cleanup(cleanup_task)
