import asyncio
import json
from datetime import datetime, timezone

import pytest

from wanxiao_client import (
    API_URL,
    DEFAULT_ROOM_QUERY_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    BoundRoom,
    NoBoundRoomsError,
    RoomQueryResult,
    WanxiaoClient,
    WanxiaoProtocolError,
    WanxiaoTransportError,
    format_query_report,
    format_room_result,
    parse_api_response,
    parse_bound_rooms,
)


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self):
        self.closed = True


class StubRoomClient(WanxiaoClient):
    def __init__(
        self,
        rooms,
        room_data,
        room_query_timeout_seconds=1,
        get_bound_rooms=None,
    ):
        super().__init__(
            "test-school",
            "test-account",
            room_query_timeout_seconds=room_query_timeout_seconds,
        )
        self._rooms = rooms
        self._room_data = room_data
        self._get_bound_rooms = get_bound_rooms

    async def get_bound_rooms(self):
        if self._get_bound_rooms is None:
            return self._rooms
        return await self._get_bound_rooms()

    async def get_room_data(self, roomverify):
        return await self._room_data(roomverify)


def api_response(body, body_as_string=True):
    return {
        "code_": 0,
        "result_": "true",
        "body": json.dumps(body) if body_as_string else body,
    }


@pytest.mark.parametrize("body_as_string", [True, False])
def test_parse_api_response_accepts_string_and_object_body(body_as_string):
    body = {"result": "0", "existflag": "0"}

    assert parse_api_response(api_response(body, body_as_string)) == body


@pytest.mark.parametrize(
    "response",
    [
        {"code_": 0, "result_": "false", "body": {"result": "0"}},
        {"code_": 0, "result_": "true", "body": {"result": "1"}},
    ],
)
def test_parse_api_response_rejects_protocol_failures(response):
    with pytest.raises(WanxiaoProtocolError):
        parse_api_response(response)


def test_parse_bound_rooms_handles_single_room():
    rooms = parse_bound_rooms(
        {"result": "0", "existflag": "1", "roomverify": "room-one", "roomname": "A101"}
    )

    assert rooms == [BoundRoom(roomverify="room-one", name="A101")]


def test_parse_bound_rooms_uses_roomfullname_in_formatted_report():
    rooms = parse_bound_rooms(
        {
            "result": "0",
            "existflag": "1",
            "roomverify": "room-one",
            "roomfullname": "南苑 1 栋 101",
            "room": "generic-room-name",
        }
    )

    report = format_query_report(
        [
            RoomQueryResult(
                room=rooms[0],
                data={"modlist": [{"bussnesstype": "0", "devicename": "电表"}]},
            )
        ]
    )

    assert "【南苑 1 栋 101】" in report
    assert "generic-room-name" not in report


def test_parse_bound_rooms_deduplicates_multiple_rooms():
    rooms = parse_bound_rooms(
        {
            "result": "0",
            "existflag": "2",
            "roomlist": [
                {"roomverify": "first-room", "roomname": "A101"},
                {"roomverify": "first-room", "roomname": "duplicate"},
                {"roomverify": "second-room", "roomname": "B202"},
            ],
        }
    )

    assert rooms == [
        BoundRoom(roomverify="first-room", name="A101"),
        BoundRoom(roomverify="second-room", name="B202"),
    ]


def test_parse_bound_rooms_returns_empty_when_nothing_is_bound():
    assert parse_bound_rooms({"result": "0", "existflag": "0"}) == []


@pytest.mark.parametrize(
    "body",
    [
        {"result": "0"},
        {"result": "0", "existflag": None},
        {"result": "0", "existflag": "unknown"},
    ],
)
def test_parse_bound_rooms_rejects_missing_or_unknown_flag(body):
    with pytest.raises(WanxiaoProtocolError):
        parse_bound_rooms(body)


@pytest.mark.parametrize(
    "body",
    [
        {"result": "0", "existflag": "1"},
        {"result": "0", "existflag": "1", "roomverify": ""},
        {"result": "0", "existflag": "1", "roomverify": "  "},
    ],
)
def test_parse_bound_rooms_rejects_single_room_without_roomverify(body):
    with pytest.raises(WanxiaoProtocolError):
        parse_bound_rooms(body)


@pytest.mark.parametrize(
    "roomlist",
    [
        [],
        "not-a-list",
        [{"roomverify": "valid-room"}, "not-a-room"],
        [{"roomverify": "valid-room"}, {"roomname": "missing-token"}],
        [{"roomverify": "valid-room"}, {"roomverify": "  "}],
    ],
)
def test_parse_bound_rooms_rejects_invalid_multiple_room_lists(roomlist):
    with pytest.raises(WanxiaoProtocolError):
        parse_bound_rooms({"result": "0", "existflag": "2", "roomlist": roomlist})


@pytest.mark.parametrize(
    "entry, expected, unexpected",
    [
        (
            {"bussnesstype": "0", "devicename": "电表", "odd": "10"},
            "电费",
            "水费",
        ),
        (
            {"businesstype": "1", "devicename": "水表", "odd": "8"},
            "水费",
            "电费",
        ),
    ],
)
def test_format_room_result_supports_single_energy_type(entry, expected, unexpected):
    result = RoomQueryResult(
        room=BoundRoom(roomverify="room-token", name="A101"),
        data={"modlist": [entry]},
    )

    text = format_room_result(result)

    assert expected in text
    assert unexpected not in text
    assert "今日用量：--" in text
    assert "累计购入：--" in text
    assert "状态：--" in text


def test_format_query_report_marks_partial_room_failure():
    results = [
        RoomQueryResult(
            room=BoundRoom(roomverify="first-room", name="A101"),
            data={"modlist": [{"bussnesstype": "0", "devicename": "电表"}]},
        ),
        RoomQueryResult(
            room=BoundRoom(roomverify="second-room", name="B202"),
            data=None,
            failed=True,
        ),
    ]

    text = format_query_report(results)

    assert "A101" in text
    assert "B202" in text
    assert "查询失败。" in text


def test_format_query_report_returns_service_message_when_all_rooms_fail():
    results = [
        RoomQueryResult(
            room=BoundRoom(roomverify="first-room", name="A101"),
            data=None,
            failed=True,
        ),
        RoomQueryResult(
            room=BoundRoom(roomverify="second-room", name="B202"),
            data=None,
            failed=True,
        ),
    ]

    assert format_query_report(results) == "水电查询服务暂时不可用，请稍后再试。"


def test_client_builds_documented_parameters_and_utc8_timestamp():
    session = FakeSession(
        [
            FakeResponse(
                200,
                api_response(
                    {
                        "result": "0",
                        "existflag": "1",
                        "roomverify": "room-token",
                        "roomname": "A101",
                    }
                ),
            ),
            FakeResponse(
                200,
                api_response(
                    {
                        "result": "0",
                        "modlist": [{"bussnesstype": "0", "devicename": "电表"}],
                    }
                ),
            ),
        ]
    )
    client = WanxiaoClient(
        school_code="001",
        student_account="sample-account",
        session=session,
        now_provider=lambda: datetime(2024, 1, 1, 16, 2, 3, tzinfo=timezone.utc),
    )

    results = asyncio.run(client.query_bound_rooms())

    assert len(results) == 1
    assert all(call["url"] == API_URL for call in session.calls)
    assert all(
        call["timeout"].total == DEFAULT_TIMEOUT_SECONDS for call in session.calls
    )
    assert session.calls[0]["params"] == {
        "param": '{"cmd":"getbindroom","account":"sample-account"}',
        "customercode": "001",
        "method": "getbindroom",
    }
    second_payload = json.loads(session.calls[1]["params"]["param"])
    assert second_payload == {
        "cmd": "h5_getstuindexpage",
        "roomverify": "room-token",
        "account": "sample-account",
        "timestamp": "20240102000203000",
    }
    assert session.calls[1]["params"]["method"] == "h5_getstuindexpage"


def test_client_retries_timeout_once():
    session = FakeSession(
        [
            asyncio.TimeoutError(),
            FakeResponse(
                200,
                api_response(
                    {"result": "0", "existflag": "1", "roomverify": "room-token"}
                ),
            ),
        ]
    )
    client = WanxiaoClient("001", "sample-account", session=session)

    rooms = asyncio.run(client.get_bound_rooms())

    assert rooms == [BoundRoom(roomverify="room-token", name="房间 1")]
    assert len(session.calls) == 2


def test_client_retries_server_error_once():
    session = FakeSession(
        [
            FakeResponse(503),
            FakeResponse(
                200,
                api_response(
                    {"result": "0", "existflag": "1", "roomverify": "room-token"}
                ),
            ),
        ]
    )
    client = WanxiaoClient("001", "sample-account", session=session)

    rooms = asyncio.run(client.get_bound_rooms())

    assert len(rooms) == 1
    assert len(session.calls) == 2


def test_client_does_not_retry_client_error_status():
    session = FakeSession([FakeResponse(400)])
    client = WanxiaoClient("001", "sample-account", session=session)

    with pytest.raises(WanxiaoProtocolError):
        asyncio.run(client.get_bound_rooms())

    assert len(session.calls) == 1


def test_client_rejects_requests_after_close_without_creating_a_session(monkeypatch):
    external_session = FakeSession([])
    client = WanxiaoClient("001", "sample-account", session=external_session)
    created_sessions = []

    def create_session(*_args, **_kwargs):
        session = FakeSession([])
        created_sessions.append(session)
        return session

    monkeypatch.setattr("wanxiao_client.aiohttp.ClientSession", create_session)

    async def close_then_request():
        await client.close()
        with pytest.raises(WanxiaoTransportError, match="客户端已关闭"):
            await client.get_bound_rooms()

    asyncio.run(close_then_request())

    assert external_session.closed is False
    assert created_sessions == []


def test_client_continues_when_one_room_fails():
    session = FakeSession(
        [
            FakeResponse(
                200,
                api_response(
                    {
                        "result": "0",
                        "existflag": "2",
                        "roomlist": [
                            {"roomverify": "first-room", "roomname": "A101"},
                            {"roomverify": "second-room", "roomname": "B202"},
                        ],
                    }
                ),
            ),
            FakeResponse(
                200,
                api_response(
                    {
                        "result": "0",
                        "modlist": [{"bussnesstype": "0", "devicename": "电表"}],
                    }
                ),
            ),
            FakeResponse(400),
        ]
    )
    client = WanxiaoClient("001", "sample-account", session=session)

    results = asyncio.run(client.query_bound_rooms())

    assert [result.failed for result in results] == [False, True]
    assert len(session.calls) == 3


def test_client_raises_for_no_bound_rooms():
    session = FakeSession(
        [FakeResponse(200, api_response({"result": "0", "existflag": "0"}))]
    )
    client = WanxiaoClient("001", "sample-account", session=session)

    with pytest.raises(NoBoundRoomsError):
        asyncio.run(client.query_bound_rooms())


def test_client_uses_a_30_second_default_room_query_deadline():
    client = WanxiaoClient("test-school", "test-account")

    assert DEFAULT_ROOM_QUERY_TIMEOUT_SECONDS == 30
    assert client._room_query_timeout_seconds == DEFAULT_ROOM_QUERY_TIMEOUT_SECONDS


def test_client_deadline_covers_binding_and_room_queries():
    deadline = 0.1
    rooms = [BoundRoom(roomverify="only-room", name="A101")]
    cancelled = []

    async def get_bound_rooms():
        await asyncio.sleep(0.06)
        return rooms

    async def get_room_data(roomverify):
        try:
            await asyncio.sleep(deadline)
        except asyncio.CancelledError:
            cancelled.append(roomverify)
            raise

    client = StubRoomClient(
        rooms,
        get_room_data,
        room_query_timeout_seconds=deadline,
        get_bound_rooms=get_bound_rooms,
    )

    async def query_with_watchdog():
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        results = await asyncio.wait_for(
            client.query_bound_rooms(), timeout=deadline + 0.08
        )
        return results, loop.time() - started_at

    results, elapsed = asyncio.run(query_with_watchdog())

    assert [result.failed for result in results] == [True]
    assert elapsed <= deadline + 0.03
    assert cancelled == ["only-room"]


def test_client_raises_transport_error_when_binding_exhausts_deadline():
    deadline = 0.05
    cancelled = []
    active = 0

    async def get_bound_rooms():
        nonlocal active
        active += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append("binding")
            raise
        finally:
            active -= 1

    async def get_room_data(roomverify):
        raise AssertionError("room query should not start")

    client = StubRoomClient(
        [],
        get_room_data,
        room_query_timeout_seconds=deadline,
        get_bound_rooms=get_bound_rooms,
    )

    async def query_with_watchdog():
        with pytest.raises(WanxiaoTransportError) as error_info:
            await asyncio.wait_for(client.query_bound_rooms(), timeout=deadline + 0.05)
        return error_info.value

    error = asyncio.run(query_with_watchdog())

    assert str(error) == "水电查询超时"
    assert cancelled == ["binding"]
    assert active == 0


def test_client_propagates_cancellation_and_cleans_up_binding_task():
    started = asyncio.Event()
    cancelled = []
    active = 0

    async def get_bound_rooms():
        nonlocal active
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append("binding")
            raise
        finally:
            active -= 1

    async def get_room_data(roomverify):
        raise AssertionError("room query should not start")

    client = StubRoomClient(
        [],
        get_room_data,
        room_query_timeout_seconds=0.05,
        get_bound_rooms=get_bound_rooms,
    )

    async def cancel_with_watchdog():
        query_task = asyncio.create_task(client.query_bound_rooms())
        await asyncio.wait_for(started.wait(), timeout=0.1)
        query_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(query_task, timeout=0.1)

    asyncio.run(cancel_with_watchdog())

    assert cancelled == ["binding"]
    assert active == 0


def test_client_limits_room_queries_to_two():
    rooms = [
        BoundRoom(roomverify="first", name="A101"),
        BoundRoom(roomverify="second", name="A102"),
        BoundRoom(roomverify="third", name="A103"),
        BoundRoom(roomverify="fourth", name="A104"),
    ]
    active = 0
    peak_active = 0

    async def get_room_data(roomverify):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.01)
            return {"roomverify": roomverify}
        finally:
            active -= 1

    client = StubRoomClient(rooms, get_room_data)
    results = asyncio.run(client.query_bound_rooms())

    assert peak_active <= 2
    assert [result.failed for result in results] == [False, False, False, False]


def test_client_keeps_bound_room_order_when_queries_finish_out_of_order():
    rooms = [
        BoundRoom(roomverify="first", name="A101"),
        BoundRoom(roomverify="second", name="A102"),
        BoundRoom(roomverify="third", name="A103"),
    ]
    completion_order = []
    delays = {"first": 0.03, "second": 0.005, "third": 0.005}

    async def get_room_data(roomverify):
        await asyncio.sleep(delays[roomverify])
        completion_order.append(roomverify)
        return {"roomverify": roomverify}

    client = StubRoomClient(rooms, get_room_data)
    results = asyncio.run(client.query_bound_rooms())

    assert completion_order == ["second", "third", "first"]
    assert [result.room.roomverify for result in results] == [
        "first",
        "second",
        "third",
    ]


def test_client_marks_deadline_tasks_failed_and_cancels_them():
    rooms = [
        BoundRoom(roomverify="first", name="A101"),
        BoundRoom(roomverify="second", name="A102"),
        BoundRoom(roomverify="third", name="A103"),
    ]
    active = 0
    started = set()
    cancelled = set()
    two_started = asyncio.Event()

    async def get_room_data(roomverify):
        nonlocal active
        active += 1
        started.add(roomverify)
        if active == 2:
            two_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(roomverify)
            raise
        finally:
            active -= 1

    client = StubRoomClient(rooms, get_room_data, room_query_timeout_seconds=0.05)

    async def query_after_two_rooms_start():
        query_task = asyncio.create_task(client.query_bound_rooms())
        await asyncio.wait_for(two_started.wait(), timeout=0.1)
        return await asyncio.wait_for(query_task, timeout=0.1)

    results = asyncio.run(query_after_two_rooms_start())

    assert [result.failed for result in results] == [True, True, True]
    assert started == {"first", "second"}
    assert cancelled == started
    assert active == 0


def test_client_marks_wanxiao_errors_as_room_failures():
    rooms = [
        BoundRoom(roomverify="working", name="A101"),
        BoundRoom(roomverify="failing", name="A102"),
    ]

    async def get_room_data(roomverify):
        if roomverify == "failing":
            raise WanxiaoTransportError("expected failure")
        return {"roomverify": roomverify}

    client = StubRoomClient(rooms, get_room_data)
    results = asyncio.run(client.query_bound_rooms())

    assert [result.failed for result in results] == [False, True]


def test_client_propagates_unexpected_room_query_errors():
    rooms = [
        BoundRoom(roomverify="broken", name="A101"),
        BoundRoom(roomverify="cancelled", name="A102"),
    ]
    cancelled = []

    async def get_room_data(roomverify):
        if roomverify == "broken":
            await asyncio.sleep(0)
            raise AssertionError("unexpected program error")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(roomverify)
            raise

    client = StubRoomClient(rooms, get_room_data)

    async def query_with_watchdog():
        with pytest.raises(AssertionError, match="unexpected program error"):
            await asyncio.wait_for(client.query_bound_rooms(), timeout=0.1)

    asyncio.run(query_with_watchdog())

    assert cancelled == ["cancelled"]
