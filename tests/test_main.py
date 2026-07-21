import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

from wanxiao_client import (
    BoundRoom,
    NoBoundRoomsError,
    RoomQueryResult,
    WanxiaoTransportError,
)


class FakeEvent:
    def plain_result(self, text):
        return text


class FakeSession:
    def __init__(self):
        self.closed = False
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.closed = True


class ResultClient:
    def __init__(self, results=None, error=None):
        self.results = results if results is not None else sample_results()
        self.error = error
        self.close_calls = 0

    async def query_bound_rooms(self):
        if self.error is not None:
            raise self.error
        return self.results

    async def close(self):
        self.close_calls += 1


def sample_results(room_name="A101"):
    return [
        RoomQueryResult(
            room=BoundRoom(roomverify="room-token", name=room_name),
            data={"modlist": [{"bussnesstype": "0", "devicename": "电表"}]},
        )
    ]


def account_entry(
    *,
    name="",
    enabled=True,
    school_name="测试大学",
    student_account="2024000001",
    **extra,
):
    return {
        "name": name,
        "enabled": enabled,
        "school_name": school_name,
        "student_account": student_account,
        **extra,
    }


class _TestSchoolDirectory:
    codes = {
        "测试大学": "100",
        "第二测试大学": "200",
        "前导零学校": "001",
    }

    def __init__(self, main):
        self.main = main

    def resolve(self, school_name):
        if not isinstance(school_name, str) or not school_name.strip():
            raise self.main.InvalidSchoolNameError()
        code = self.codes.get(" ".join(school_name.split()))
        if code is None:
            raise self.main.SchoolNotFoundError()
        return code


def load_main_with_fake_astrbot(
    monkeypatch, module_name="main", use_test_school_directory=True
):
    astrbot_module = types.ModuleType("astrbot")
    api_module = types.ModuleType("astrbot.api")
    event_module = types.ModuleType("astrbot.api.event")
    star_module = types.ModuleType("astrbot.api.star")

    class FakeFilter:
        PermissionType = types.SimpleNamespace(ADMIN="ADMIN")

        @staticmethod
        def command(name):
            def decorate(function):
                function.command_name = name
                return function

            return decorate

        @staticmethod
        def permission_type(permission):
            def decorate(function):
                function.required_permission = permission
                return function

            return decorate

    class FakeStar:
        def __init__(self, context):
            self.context = context

    api_module.AstrBotConfig = dict
    event_module.AstrMessageEvent = object
    event_module.filter = FakeFilter
    star_module.Context = object
    star_module.Star = FakeStar
    astrbot_module.api = api_module
    api_module.event = event_module
    api_module.star = star_module

    monkeypatch.setitem(sys.modules, "astrbot", astrbot_module)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    main = importlib.import_module(module_name)
    if use_test_school_directory:
        main.SCHOOL_DIRECTORY = _TestSchoolDirectory(main)
    return main


def test_package_load_uses_relative_dependency_imports(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    package_name = root.name
    module_name = "{}.main".format(package_name)
    client_module_name = "{}.wanxiao_client".format(package_name)
    directory_module_name = "{}.school_directory".format(package_name)
    sentinel_client = types.ModuleType("wanxiao_client")
    sentinel_client.DEFAULT_TIMEOUT_SECONDS = object()
    sentinel_client.NoBoundRoomsError = type("NoBoundRoomsError", (Exception,), {})
    sentinel_client.WanxiaoClient = type("WanxiaoClient", (), {})
    sentinel_client.WanxiaoError = type("WanxiaoError", (Exception,), {})
    sentinel_client.format_query_report = lambda results: "sentinel"
    sentinel_directory = types.ModuleType("school_directory")
    for name in (
        "AmbiguousSchoolNameError",
        "InvalidSchoolNameError",
        "SchoolDirectory",
        "SchoolDirectoryUnavailableError",
        "SchoolNotFoundError",
    ):
        setattr(sentinel_directory, name, type(name, (Exception,), {}))

    monkeypatch.syspath_prepend(str(root.parent))
    monkeypatch.delitem(sys.modules, package_name, raising=False)
    monkeypatch.delitem(sys.modules, client_module_name, raising=False)
    monkeypatch.delitem(sys.modules, directory_module_name, raising=False)
    monkeypatch.setitem(sys.modules, "wanxiao_client", sentinel_client)
    monkeypatch.setitem(sys.modules, "school_directory", sentinel_directory)

    main = load_main_with_fake_astrbot(monkeypatch, module_name)
    package_client = sys.modules.get(client_module_name)
    package_directory = sys.modules.get(directory_module_name)

    assert package_client is not None
    assert package_directory is not None
    assert main.WanxiaoClient is package_client.WanxiaoClient
    assert main.WanxiaoClient.__module__ == client_module_name
    assert main.WanxiaoClient is not sentinel_client.WanxiaoClient
    assert main.SchoolDirectory is package_directory.SchoolDirectory
    assert main.SchoolDirectory is not sentinel_directory.SchoolDirectory


async def collect(async_generator):
    return [item async for item in async_generator]


def test_schema_and_metadata_declare_template_list_and_minimum_version():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    metadata = yaml.safe_load((root / "metadata.yaml").read_text(encoding="utf-8"))

    account_schema = schema["accounts"]
    template = account_schema["templates"]["wanxiao_account"]
    assert account_schema["type"] == "template_list"
    assert template["name"] == "完美校园账号"
    assert "display_item" not in template
    assert template["items"]["name"]["type"] == "string"
    assert template["items"]["enabled"] == {
        "type": "bool",
        "description": "启用该账号",
        "default": True,
    }
    assert template["items"]["school_name"] == {
        "type": "string",
        "description": "学校名称",
        "hint": "请填写完整学校名称，学校代码会自动解析。",
        "default": "",
    }
    assert template["items"]["student_account"]["default"] == ""

    def schema_keys(value):
        if isinstance(value, dict):
            return set(value).union(*(schema_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(schema_keys(item) for item in value))
        return set()

    assert "school_code" not in schema_keys(schema)
    assert "student_account" not in schema
    assert metadata["version"] == "v2.0.0"
    assert metadata["astrbot_version"] == ">=4.10.4"


def test_plugin_loads_without_account_configuration(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(context=None, config={})

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert messages == ["请先在插件配置中添加并启用账号。"]
    assert plugin._session is None
    assert (
        main.WanxiaoElectricityPlugin.query_water_and_electricity.command_name
        == "查水电"
    )
    assert (
        main.WanxiaoElectricityPlugin.query_water_and_electricity.required_permission
        == "ADMIN"
    )


def test_template_list_parsing_preserves_leading_zero_and_hides_secrets(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(
                    name="  宿舍  ",
                    school_name="  前导零学校 ",
                    student_account=" 00001234 ",
                    __template_key="wanxiao_account",
                )
            ]
        },
    )

    accounts = plugin._get_accounts()

    assert accounts == [
        main.AccountConfig(name="宿舍", school_code="001", student_account="00001234")
    ]
    representation = repr(accounts[0])
    assert "001" not in representation
    assert "00001234" not in representation
    assert "****1234" in representation

    captured = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(main, "WanxiaoClient", CapturingClient)
    plugin._session = FakeSession()
    plugin._get_client(accounts[0].credentials)
    assert captured["school_code"] == "001"
    assert captured["student_account"] == "00001234"


def test_bundled_directory_resolves_production_school_before_client_construction(
    monkeypatch,
):
    main = load_main_with_fake_astrbot(
        monkeypatch, use_test_school_directory=False
    )
    assert isinstance(main.SCHOOL_DIRECTORY, main.SchoolDirectory)

    captured = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    def unexpected_network_resource(*args, **kwargs):
        pytest.fail("production directory integration test must stay offline")

    monkeypatch.setattr(main, "WanxiaoClient", CapturingClient)
    monkeypatch.setattr(main.aiohttp, "ClientSession", unexpected_network_resource)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(
                    school_name="郑州大学", student_account="2024000001"
                )
            ]
        },
    )

    accounts = plugin._get_accounts()

    assert accounts == [
        main.AccountConfig(
            name="", school_code="11", student_account="2024000001"
        )
    ]
    plugin._session = FakeSession()
    plugin._get_client(accounts[0].credentials)
    assert captured["school_code"] == "11"
    assert captured["student_account"] == "2024000001"
    assert captured["session"] is plugin._session


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"accounts": []},
        {"school_code": "001", "student_account": "00001234"},
        {
            "accounts": [],
            "school_code": "001",
            "student_account": "00001234",
        },
    ],
)
def test_missing_or_empty_accounts_never_read_legacy_fields(monkeypatch, config):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(context=None, config=config)

    assert plugin._get_accounts() == []


def test_accounts_ignore_residual_legacy_fields(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(
                    student_account="2024000002", school_code="legacy-code"
                )
            ],
            "school_code": "100",
            "student_account": "2024000001",
        },
    )

    accounts = plugin._get_accounts()
    assert [account.student_account for account in accounts] == ["2024000002"]
    assert accounts[0].school_code == "100"


def test_legacy_school_code_only_entry_requires_migration(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                {"school_code": "001", "student_account": "2024000001"}
            ]
        },
    )

    with pytest.raises(
        main.AccountConfigurationError, match="需要从旧版配置迁移"
    ) as error:
        plugin._get_accounts()

    assert "001" not in str(error.value)
    assert "school_code" not in str(error.value)


def test_unmatched_school_name_reports_entry_index_without_secrets(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    student_account = "sensitive-student-account"
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(enabled=False),
                account_entry(
                    school_name="未收录学校", student_account=student_account
                ),
            ]
        },
    )

    with pytest.raises(main.AccountConfigurationError) as error:
        plugin._get_accounts()

    assert str(error.value) == (
        "第 2 个账号的学校名称未在内置学校列表中找到，请填写完整名称。"
    )
    assert student_account not in str(error.value)


def test_unavailable_directory_does_not_leak_account_details(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    student_account = "sensitive-student-account"

    class UnavailableDirectory:
        def resolve(self, school_name):
            raise main.SchoolDirectoryUnavailableError()

    main.SCHOOL_DIRECTORY = UnavailableDirectory()
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(student_account=student_account, school_code="private")
            ]
        },
    )

    with pytest.raises(main.AccountConfigurationError) as error:
        plugin._get_accounts()

    assert str(error.value) == "内置学校列表不可用，请联系插件维护者。"
    assert student_account not in str(error.value)
    assert "private" not in str(error.value)


def test_nonempty_disabled_or_invalid_accounts_keep_existing_errors(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    disabled = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [account_entry(enabled=False)],
            "school_code": "100",
            "student_account": "2024000001",
        },
    )
    invalid = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [account_entry(student_account="")],
            "school_code": "100",
            "student_account": "2024000001",
        },
    )
    disabled._get_client = lambda credentials: pytest.fail("legacy fallback was used")
    invalid._get_client = lambda credentials: pytest.fail("legacy fallback was used")

    disabled_messages = asyncio.run(
        collect(disabled.query_water_and_electricity(FakeEvent()))
    )
    invalid_messages = asyncio.run(
        collect(invalid.query_water_and_electricity(FakeEvent()))
    )

    assert disabled_messages == ["未找到已启用的有效账号，请检查 accounts 配置。"]
    assert invalid_messages == ["账号配置不正确，请检查 accounts 配置。"]


def test_duplicate_credentials_keep_first_enabled_entry(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(name="首项"),
                account_entry(name="重复项", __template_key="wanxiao_account"),
                account_entry(
                    name="禁用项", enabled=False, student_account="2024000002"
                ),
            ]
        },
    )

    accounts = plugin._get_accounts()

    assert [account.name for account in accounts] == ["首项"]


def test_account_limit_accepts_sixteen_and_rejects_seventeen(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    entries = [
        account_entry(student_account="2024{:06d}".format(index)) for index in range(16)
    ]
    accepted = main.WanxiaoElectricityPlugin(context=None, config={"accounts": entries})
    rejected = main.WanxiaoElectricityPlugin(
        context=None,
        config={"accounts": entries + [account_entry(student_account="2024999999")]},
    )

    assert len(accepted._get_accounts()) == 16
    with pytest.raises(main.AccountConfigurationError, match="最多只能配置 16"):
        rejected._get_accounts()


@pytest.mark.parametrize(
    "accounts",
    [
        None,
        {},
        (account_entry(),),
        [None],
        [account_entry(enabled="true")],
        [account_entry(name="x" * 33)],
        [account_entry(name="bad\x00name")],
        [account_entry(school_name=None)],
        [account_entry(school_name="")],
        [account_entry(student_account="2024\t000001")],
        [account_entry(school_name="未收录学校")],
        [account_entry(student_account="")],
    ],
)
def test_invalid_template_list_shapes_and_fields_are_safe(monkeypatch, accounts):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": accounts,
            "school_code": "100",
            "student_account": "2024000001",
        },
    )

    with pytest.raises(main.AccountConfigurationError) as error:
        plugin._get_accounts()

    assert "2024000001" not in str(error.value)
    assert "100" not in str(error.value)


def test_account_labels_mask_student_account_and_disambiguate_names(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    named = main.AccountConfig(
        name="宿舍", school_code="100", student_account="2024000001"
    )
    unnamed = main.AccountConfig(name="", school_code="100", student_account="1234")

    assert (
        main.WanxiaoElectricityPlugin._account_label(named) == "宿舍（学号 ****0001）"
    )
    assert main.WanxiaoElectricityPlugin._account_label(unnamed) == "账号 ****"
    assert "2024000001" not in repr(named)
    assert "100" not in repr(named)


def test_multiple_successful_accounts_are_reported_in_configuration_order(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    first = account_entry(name="甲", student_account="2024000001")
    second = account_entry(name="乙", student_account="2024000002")
    plugin = main.WanxiaoElectricityPlugin(
        context=None, config={"accounts": [first, second]}
    )
    clients = {
        ("100", "2024000001"): ResultClient(sample_results("A101")),
        ("100", "2024000002"): ResultClient(sample_results("B202")),
    }
    plugin._get_client = lambda credentials: clients[credentials]

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert len(messages) == 2
    assert messages[0].startswith("【1. 甲（学号 ****0001）】")
    assert "A101" in messages[0]
    assert messages[1].startswith("【2. 乙（学号 ****0002）】")
    assert "B202" in messages[1]


def test_same_name_and_suffix_accounts_remain_distinguishable(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    first_account = "2024001234"
    second_account = "2025001234"
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(
                    name="宿舍",
                    school_name="测试大学",
                    student_account=first_account,
                ),
                account_entry(
                    name="宿舍",
                    school_name="第二测试大学",
                    student_account=second_account,
                ),
            ]
        },
    )
    plugin._get_client = lambda credentials: ResultClient()

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert messages[0].startswith("【1. 宿舍（学号 ****1234）】")
    assert messages[1].startswith("【2. 宿舍（学号 ****1234）】")
    assert messages[0] != messages[1]
    combined = "\n".join(messages)
    assert first_account not in combined
    assert second_account not in combined
    assert "100" not in combined
    assert "200" not in combined


def test_no_rooms_and_service_errors_do_not_block_other_accounts(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(name="无房间", student_account="2024000001"),
                account_entry(name="服务错误", student_account="2024000002"),
                account_entry(name="正常", student_account="2024000003"),
            ]
        },
    )
    clients = {
        ("100", "2024000001"): ResultClient(error=NoBoundRoomsError("no rooms")),
        ("100", "2024000002"): ResultClient(
            error=WanxiaoTransportError("service error")
        ),
        ("100", "2024000003"): ResultClient(sample_results("C303")),
    }
    plugin._get_client = lambda credentials: clients[credentials]

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert "未找到已绑定的房间。" in messages[0]
    assert "水电查询服务暂时不可用，请稍后再试。" in messages[1]
    assert "C303" in messages[2]


def test_account_queries_never_exceed_two_concurrent_requests(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    tracker = {"active": 0, "peak": 0}
    two_started = asyncio.Event()
    release = asyncio.Event()

    class BlockingClient:
        async def query_bound_rooms(self):
            tracker["active"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["active"])
            if tracker["active"] == 2:
                two_started.set()
            try:
                await release.wait()
                return sample_results()
            finally:
                tracker["active"] -= 1

    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(student_account="2024000001"),
                account_entry(student_account="2024000002"),
                account_entry(student_account="2024000003"),
            ]
        },
    )
    plugin._get_client = lambda credentials: BlockingClient()

    async def run_query():
        task = asyncio.create_task(
            collect(plugin.query_water_and_electricity(FakeEvent()))
        )
        await asyncio.wait_for(two_started.wait(), timeout=0.2)
        assert tracker["peak"] == 2
        release.set()
        return await asyncio.wait_for(task, timeout=0.2)

    messages = asyncio.run(run_query())

    assert len(messages) == 3
    assert tracker["peak"] == 2
    assert tracker["active"] == 0


def test_concurrent_commands_share_account_limit_and_queue_second_batch(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    tracker = {"active": 0, "peak": 0, "started": 0}
    first_batch_started = asyncio.Event()
    second_batch_started = asyncio.Event()
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    class BlockingClient:
        async def query_bound_rooms(self):
            tracker["started"] += 1
            invocation = tracker["started"]
            tracker["active"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["active"])
            if invocation == 2:
                first_batch_started.set()
            if invocation == 4:
                second_batch_started.set()
            try:
                if invocation <= 2:
                    await first_release.wait()
                else:
                    await second_release.wait()
                return sample_results()
            finally:
                tracker["active"] -= 1

    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(student_account="2024000001"),
                account_entry(student_account="2024000002"),
            ]
        },
    )
    plugin._get_client = lambda credentials: BlockingClient()

    async def run_queries():
        first_query = asyncio.create_task(
            collect(plugin.query_water_and_electricity(FakeEvent()))
        )
        await asyncio.wait_for(first_batch_started.wait(), timeout=1)

        second_query = asyncio.create_task(
            collect(plugin.query_water_and_electricity(FakeEvent()))
        )
        await asyncio.sleep(0)
        assert tracker["started"] == 2
        assert second_batch_started.is_set() is False

        first_release.set()
        first_messages = await asyncio.wait_for(first_query, timeout=1)
        await asyncio.wait_for(second_batch_started.wait(), timeout=1)
        assert tracker["peak"] == 2

        second_release.set()
        second_messages = await asyncio.wait_for(second_query, timeout=1)
        return first_messages, second_messages

    first_messages, second_messages = asyncio.run(run_queries())

    assert len(first_messages) == 2
    assert len(second_messages) == 2
    assert tracker["peak"] == 2
    assert tracker["active"] == 0


def test_cancellation_cleans_up_all_account_tasks(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    started = 0
    two_started = asyncio.Event()
    waiting = asyncio.Event()
    clients = []

    class WaitingClient:
        def __init__(self):
            self.cancelled = False

        async def query_bound_rooms(self):
            nonlocal started
            started += 1
            if started == 2:
                two_started.set()
            try:
                await waiting.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(student_account="2024000001"),
                account_entry(student_account="2024000002"),
            ]
        },
    )

    def get_client(credentials):
        client = WaitingClient()
        clients.append(client)
        return client

    plugin._get_client = get_client

    async def cancel_query():
        task = asyncio.create_task(
            collect(plugin.query_water_and_electricity(FakeEvent()))
        )
        await asyncio.wait_for(two_started.wait(), timeout=0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_query())

    assert len(clients) == 2
    assert all(client.cancelled for client in clients)
    assert plugin._account_semaphore._value == 2
    assert plugin._lifecycle_lock.locked() is False


def test_program_errors_cancel_other_tasks_and_propagate(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    waiting_started = asyncio.Event()

    class FailingClient:
        async def query_bound_rooms(self):
            await waiting_started.wait()
            raise AssertionError("unexpected program error")

    class WaitingClient:
        def __init__(self):
            self.cancelled = False

        async def query_bound_rooms(self):
            waiting_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    waiting_client = WaitingClient()
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={
            "accounts": [
                account_entry(student_account="2024000001"),
                account_entry(student_account="2024000002"),
            ]
        },
    )
    plugin._get_client = lambda credentials: (
        FailingClient() if credentials[1] == "2024000001" else waiting_client
    )

    with pytest.raises(AssertionError, match="unexpected program error"):
        asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert waiting_client.cancelled is True


def test_client_cache_uses_one_session_evicts_stale_clients_and_terminates_once(
    monkeypatch,
):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(context=None, config={})
    session = FakeSession()
    plugin._session = session
    first_credentials = ("100", "2024000001")
    second_credentials = ("100", "2024000002")

    first_client = plugin._get_client(first_credentials)
    second_client = plugin._get_client(second_credentials)

    assert first_client._session is session
    assert second_client._session is session
    assert first_client is plugin._get_client(first_credentials)

    async def manage_clients():
        active = [
            main.AccountConfig(name="", school_code="100", student_account="2024000002")
        ]
        clients = await plugin._sync_clients(active)
        assert clients == [second_client]
        assert first_client._closed is True
        assert session.closed is False
        await plugin.terminate()
        await plugin.terminate()

    asyncio.run(manage_clients())

    assert second_client._closed is True
    assert session.close_calls == 1
    assert plugin._clients == {}
    assert plugin._session is None


def test_terminate_waits_for_active_query_before_closing_resources(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    credentials = ("100", "2024000001")
    session = FakeSession()
    query_started = asyncio.Event()
    release_query = asyncio.Event()

    class BlockingClient:
        def __init__(self):
            self.close_calls = 0

        async def query_bound_rooms(self):
            query_started.set()
            await release_query.wait()
            return sample_results()

        async def close(self):
            self.close_calls += 1

    client = BlockingClient()
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={"accounts": [account_entry(student_account=credentials[1])]},
    )
    plugin._session = session
    plugin._clients[credentials] = client

    async def query_then_terminate():
        query_task = asyncio.create_task(
            collect(plugin.query_water_and_electricity(FakeEvent()))
        )
        await asyncio.wait_for(query_started.wait(), timeout=1)

        terminate_task = asyncio.create_task(plugin.terminate())
        await asyncio.sleep(0)
        assert terminate_task.done() is False
        assert client.close_calls == 0
        assert session.closed is False

        release_query.set()
        messages = await asyncio.wait_for(query_task, timeout=1)
        await asyncio.wait_for(terminate_task, timeout=1)
        return messages

    messages = asyncio.run(query_then_terminate())

    assert len(messages) == 1
    assert client.close_calls == 1
    assert session.close_calls == 1
    assert plugin._terminated is True
    assert plugin._clients == {}
    assert plugin._session is None


def test_terminate_finishes_cleanup_before_propagating_cancellation(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    credentials = ("100", "2024000001")

    class BlockingCloseSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self):
            self.close_calls += 1
            self.close_started.set()
            await self.release_close.wait()
            self.closed = True

    class TrackingClient:
        def __init__(self):
            self.closed = False
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1
            self.closed = True

    session = BlockingCloseSession()
    client = TrackingClient()
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={"accounts": [account_entry(student_account=credentials[1])]},
    )
    plugin._session = session
    plugin._clients[credentials] = client

    async def cancel_terminate_during_close():
        terminate_task = asyncio.create_task(plugin.terminate())
        await asyncio.wait_for(session.close_started.wait(), timeout=1)
        assert client.closed is True
        assert client.close_calls == 1
        assert plugin._cleanup_task is not None
        assert plugin._cleanup_task.done() is False

        terminate_task.cancel()
        await asyncio.sleep(0)
        assert terminate_task.done() is False

        session.release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(terminate_task, timeout=1)

        assert session.closed is True
        assert session.close_calls == 1
        assert client.closed is True
        assert client.close_calls == 1
        assert plugin._cleanup_task is None
        assert plugin._terminated is True

        await plugin.terminate()

    asyncio.run(cancel_terminate_during_close())

    assert session.close_calls == 1
    assert client.close_calls == 1
    assert plugin._cleanup_task is None
    assert plugin._clients == {}
    assert plugin._session is None


def test_terminated_plugin_does_not_rebuild_resources(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    credentials = ("100", "2024000001")
    session = FakeSession()
    client = ResultClient()
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={"accounts": [account_entry(student_account=credentials[1])]},
    )
    plugin._session = session
    plugin._clients[credentials] = client

    async def query_then_terminate():
        first_messages = await collect(plugin.query_water_and_electricity(FakeEvent()))
        await plugin.terminate()
        monkeypatch.setattr(
            main.aiohttp,
            "ClientSession",
            lambda *args, **kwargs: pytest.fail("terminated plugin rebuilt a session"),
        )
        stopped_messages = await collect(
            plugin.query_water_and_electricity(FakeEvent())
        )
        return first_messages, stopped_messages

    first_messages, stopped_messages = asyncio.run(query_then_terminate())

    assert len(first_messages) == 1
    assert stopped_messages == ["插件已停止，无法查询水电。"]
    assert client.close_calls == 1
    assert session.close_calls == 1
    assert plugin._clients == {}
    assert plugin._session is None


def test_report_fragmentation_keeps_all_characters_and_newlines_intact(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    account = main.AccountConfig(
        name="长报告", school_code="100", student_account="2024000001"
    )
    title = "【7. 长报告（学号 ****0001）】"
    long_line = "余额：" + "9" * (main.MAX_REPORT_CHARS + 100)
    mixed_newlines = "首行\n{}\n\n尾行\n末尾".format(
        "x" * (main.MAX_REPORT_CHARS + 100)
    )

    for report in (long_line, mixed_newlines):
        chunks = main.WanxiaoElectricityPlugin._split_account_report(
            account,
            report,
            result_index=7,
        )

        assert len(chunks) > 1
        assert "2024000001" not in "\n".join(chunks)
        reconstructed = []
        for index, chunk in enumerate(chunks, start=1):
            prefix = "{} ({}/{})\n".format(title, index, len(chunks))
            assert chunk.startswith(prefix)
            assert len(chunk) <= main.MAX_REPORT_CHARS
            reconstructed.append(chunk[len(prefix) :])
        assert "".join(reconstructed) == report
