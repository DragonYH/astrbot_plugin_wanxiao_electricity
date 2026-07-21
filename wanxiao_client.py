"""Async client and presentation helpers for the Wanxiao utility-balance API."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

import aiohttp


API_URL = "https://xqh5.17wanxiao.com/smartWaterAndElectricityService/SWAEServlet"
DEFAULT_TIMEOUT_SECONDS = 12
DEFAULT_ROOM_QUERY_TIMEOUT_SECONDS = 30
UTC_PLUS_8 = timezone(timedelta(hours=8))


class WanxiaoError(Exception):
    """Base class for expected Wanxiao API failures."""


class WanxiaoTransportError(WanxiaoError):
    """The service could not be reached or returned a server error."""


class WanxiaoProtocolError(WanxiaoError):
    """The service returned a response that does not match its documented protocol."""


class NoBoundRoomsError(WanxiaoError):
    """The configured account has no rooms bound in Wanxiao."""


@dataclass(frozen=True)
class BoundRoom:
    """A room that can be queried by its opaque room verification value."""

    roomverify: str
    name: str


@dataclass(frozen=True)
class RoomQueryResult:
    """A per-room result; failed results intentionally do not retain error details."""

    room: BoundRoom
    data: Optional[Dict[str, Any]]
    failed: bool = False


def utc8_timestamp(now: Optional[datetime] = None) -> str:
    """Return the API timestamp in UTC+8 as yyyyMMddHHmmss000."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    return now.astimezone(UTC_PLUS_8).strftime("%Y%m%d%H%M%S") + "000"


def parse_api_response(response: Any) -> Dict[str, Any]:
    """Validate and decode the two-layer response used by Wanxiao."""
    if not isinstance(response, Mapping):
        raise WanxiaoProtocolError("接口响应格式错误")

    if (
        str(response.get("code_")) != "0"
        or str(response.get("result_")).lower() != "true"
    ):
        raise WanxiaoProtocolError("接口返回失败")

    body = response.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            raise WanxiaoProtocolError("接口响应格式错误")

    if not isinstance(body, Mapping):
        raise WanxiaoProtocolError("接口响应格式错误")

    if str(body.get("result")) != "0":
        raise WanxiaoProtocolError("接口返回失败")

    return dict(body)


def _display_value(value: Any) -> str:
    if value is None:
        return "--"
    value = str(value).strip()
    return value or "--"


def _room_name(room: Mapping[str, Any], index: int) -> str:
    for key in (
        "roomfullname",
        "roomname",
        "roomName",
        "room_name",
        "room",
        "dormname",
        "dormName",
    ):
        value = room.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "房间 {}".format(index)


def parse_bound_rooms(body: Mapping[str, Any]) -> List[BoundRoom]:
    """Extract unique rooms from a validated documented binding response."""
    if not isinstance(body, Mapping):
        raise WanxiaoProtocolError("房间数据格式错误")

    existflag = str(body.get("existflag", "")).strip()
    if existflag == "0":
        return []
    if existflag == "1":
        candidates: Sequence[Any] = [body]
    elif existflag == "2":
        candidates = body.get("roomlist")
        if not isinstance(candidates, list) or not candidates:
            raise WanxiaoProtocolError("房间数据格式错误")
    else:
        raise WanxiaoProtocolError("房间数据格式错误")

    rooms: List[BoundRoom] = []
    seen = set()
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, Mapping):
            raise WanxiaoProtocolError("房间数据格式错误")

        roomverify = candidate.get("roomverify")
        if roomverify is None or not str(roomverify).strip():
            raise WanxiaoProtocolError("房间数据格式错误")

        roomverify = str(roomverify).strip()
        if roomverify in seen:
            continue
        seen.add(roomverify)
        rooms.append(
            BoundRoom(roomverify=roomverify, name=_room_name(candidate, index))
        )

    return rooms


def _energy_label(item: Mapping[str, Any]) -> Optional[str]:
    business_type = item.get("bussnesstype")
    if business_type is None or str(business_type).strip() == "":
        business_type = item.get("businesstype")
    return {"0": "电", "1": "水"}.get(str(business_type))


def format_room_result(result: RoomQueryResult) -> str:
    """Format one room without inferring units or values not provided by the API."""
    header = "【{}】".format(result.room.name)
    if result.failed:
        return "{}\n查询失败。".format(header)

    data = result.data or {}
    modlist = data.get("modlist", [])
    if not isinstance(modlist, list):
        modlist = []

    entries = []
    for item in modlist:
        if not isinstance(item, Mapping):
            continue
        energy = _energy_label(item)
        if energy is None:
            continue
        entries.append(
            "{}费\n设备：{}\n余额：{}\n今日用量：{}\n累计购入：{}\n状态：{}".format(
                energy,
                _display_value(item.get("devicename")),
                _display_value(item.get("odd")),
                _display_value(item.get("todayuse")),
                _display_value(item.get("sumbuy")),
                _display_value(item.get("status")),
            )
        )

    if not entries:
        return "{}\n暂无可用水电数据。".format(header)
    return "{}\n{}".format(header, "\n\n".join(entries))


def format_query_report(results: Sequence[RoomQueryResult]) -> str:
    """Build the user-facing report, including partial-failure handling."""
    if not results:
        return "未找到已绑定的房间。"
    if all(result.failed for result in results):
        return "水电查询服务暂时不可用，请稍后再试。"
    return "水电查询结果：\n{}".format(
        "\n\n".join(format_room_result(result) for result in results)
    )


class WanxiaoClient:
    """A small async client whose session and clock can be injected by tests."""

    def __init__(
        self,
        school_code: str,
        student_account: str,
        session: Optional[aiohttp.ClientSession] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        now_provider: Optional[Callable[[], datetime]] = None,
        room_query_timeout_seconds: float = DEFAULT_ROOM_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        self.school_code = self._config_text(school_code)
        self.student_account = self._config_text(student_account)
        self._session = session
        self._owns_session = session is None
        self._closed = False
        self._timeout_seconds = timeout_seconds
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._room_query_timeout_seconds = room_query_timeout_seconds

    @staticmethod
    def _config_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    async def close(self) -> None:
        """Close the client and any session it owns."""
        if self._closed:
            return

        self._closed = True
        session = self._session
        self._session = None
        if self._owns_session and session is not None and not session.closed:
            await session.close()

    async def get_bound_rooms(self) -> List[BoundRoom]:
        body = await self._request(
            "getbindroom",
            {"cmd": "getbindroom", "account": self.student_account},
        )
        return parse_bound_rooms(body)

    async def get_room_data(self, roomverify: str) -> Dict[str, Any]:
        body = await self._request(
            "h5_getstuindexpage",
            {
                "cmd": "h5_getstuindexpage",
                "roomverify": roomverify,
                "account": self.student_account,
                "timestamp": utc8_timestamp(self._now_provider()),
            },
        )
        return body

    async def query_bound_rooms(self) -> List[RoomQueryResult]:
        loop = asyncio.get_running_loop()
        _query_deadline = loop.time() + self._room_query_timeout_seconds
        binding_task = asyncio.create_task(self.get_bound_rooms())
        try:
            remaining = _query_deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            rooms = await asyncio.wait_for(binding_task, timeout=remaining)
        except asyncio.TimeoutError:
            await self._cancel_and_wait([binding_task])
            raise WanxiaoTransportError("水电查询超时") from None
        except asyncio.CancelledError:
            await self._cancel_and_wait([binding_task])
            raise
        except Exception:
            await self._cancel_and_wait([binding_task])
            raise

        if not rooms:
            raise NoBoundRoomsError("未找到已绑定的房间")

        semaphore = asyncio.Semaphore(2)

        async def query_room(room: BoundRoom) -> RoomQueryResult:
            try:
                # Keep both network attempts for a room within the same concurrency slot.
                async with semaphore:
                    data = await self.get_room_data(room.roomverify)
            except WanxiaoError:
                return RoomQueryResult(room=room, data=None, failed=True)
            return RoomQueryResult(room=room, data=data)

        tasks = [asyncio.create_task(query_room(room)) for room in rooms]
        remaining = _query_deadline - loop.time()
        if remaining <= 0:
            await self._cancel_and_wait(tasks)
            return [
                RoomQueryResult(room=room, data=None, failed=True) for room in rooms
            ]

        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=remaining,
                return_when=asyncio.FIRST_EXCEPTION,
            )
        except asyncio.CancelledError:
            await self._cancel_and_wait(tasks)
            raise
        except Exception:
            await self._cancel_and_wait(tasks)
            raise

        unexpected_failures = [
            task
            for task in done
            if not task.cancelled() and task.exception() is not None
        ]
        if unexpected_failures:
            await self._cancel_and_wait(tasks)
            unexpected_failures[0].result()

        if pending:
            await self._cancel_and_wait(tasks)

        return [
            task.result()
            if task in done
            else RoomQueryResult(room=room, data=None, failed=True)
            for room, task in zip(rooms, tasks)
        ]

    @staticmethod
    async def _cancel_and_wait(tasks: Sequence[asyncio.Task]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise WanxiaoTransportError("客户端已关闭")
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds)
            )
            self._owns_session = True
        return self._session

    async def _request(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        params = {
            "param": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            "customercode": self.school_code,
            "method": command,
        }

        for attempt in range(2):
            try:
                session = await self._get_session()
                timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
                async with session.post(
                    API_URL, params=params, timeout=timeout
                ) as response:
                    try:
                        status = int(response.status)
                    except (TypeError, ValueError):
                        raise WanxiaoProtocolError("接口响应格式错误")
                    if 500 <= status <= 599:
                        if attempt == 0:
                            continue
                        raise WanxiaoTransportError("服务暂时不可用")
                    if status < 200 or status >= 300:
                        raise WanxiaoProtocolError("接口请求失败")
                    try:
                        response_data = await response.json(content_type=None)
                    except (asyncio.TimeoutError, aiohttp.ClientConnectionError):
                        raise
                    except (aiohttp.ClientError, TypeError, ValueError):
                        raise WanxiaoProtocolError("接口响应格式错误")
                    return parse_api_response(response_data)
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError):
                if attempt == 0:
                    continue
                raise WanxiaoTransportError("服务暂时不可用")
            except WanxiaoError:
                raise
            except aiohttp.ClientError:
                raise WanxiaoProtocolError("接口请求失败")

        raise WanxiaoTransportError("服务暂时不可用")
