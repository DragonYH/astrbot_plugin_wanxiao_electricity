import asyncio
import importlib
import sys
import types
from pathlib import Path

import yaml

import pytest

from wanxiao_client import BoundRoom, NoBoundRoomsError, RoomQueryResult


class FakeEvent:
    def plain_result(self, text):
        return text


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class SuccessClient:
    async def query_bound_rooms(self):
        return [
            RoomQueryResult(
                room=BoundRoom(roomverify="room-token", name="A101"),
                data={"modlist": [{"bussnesstype": "0", "devicename": "电表"}]},
            )
        ]


class NoRoomsClient:
    async def query_bound_rooms(self):
        raise NoBoundRoomsError("no rooms")


class UnexpectedClient:
    async def query_bound_rooms(self):
        raise AssertionError("unexpected program error")


def load_main_with_fake_astrbot(monkeypatch):
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
    sys.modules.pop("main", None)
    return importlib.import_module("main")


async def collect(async_generator):
    return [item async for item in async_generator]


def test_plugin_loads_without_astrbot_installed_and_missing_config(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(context=None, config={})

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert messages == ["请先在插件配置中填写 school_code 和 student_account。"]
    assert plugin._session is None
    metadata_path = Path(__file__).resolve().parents[1] / "metadata.yaml"
    assert (
        yaml.safe_load(metadata_path.read_text(encoding="utf-8"))["astrbot_version"]
        == ">=3.5.19"
    )
    assert (
        main.WanxiaoElectricityPlugin.query_water_and_electricity.command_name
        == "查水电"
    )
    assert (
        main.WanxiaoElectricityPlugin.query_water_and_electricity.required_permission
        == "ADMIN"
    )


def test_plugin_preserves_string_configuration_and_formats_result(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={"school_code": "001", "student_account": "sample-account"},
    )
    plugin._get_client = lambda credentials: SuccessClient()

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert plugin._get_credentials() == ("001", "sample-account")
    assert "水电查询结果" in messages[0]
    assert "电费" in messages[0]


def test_plugin_reports_no_bound_rooms(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={"school_code": "001", "student_account": "sample-account"},
    )
    plugin._get_client = lambda credentials: NoRoomsClient()

    messages = asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))

    assert messages == ["未找到已绑定的房间。"]


def test_plugin_propagates_unexpected_client_errors(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(
        context=None,
        config={"school_code": "001", "student_account": "sample-account"},
    )
    plugin._get_client = lambda credentials: UnexpectedClient()

    with pytest.raises(AssertionError, match="unexpected program error"):
        asyncio.run(collect(plugin.query_water_and_electricity(FakeEvent())))


def test_plugin_closes_owned_session_on_terminate(monkeypatch):
    main = load_main_with_fake_astrbot(monkeypatch)
    plugin = main.WanxiaoElectricityPlugin(context=None, config={})
    session = FakeSession()
    plugin._session = session

    asyncio.run(plugin.terminate())

    assert session.closed is True
    assert plugin._session is None
