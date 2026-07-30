import subprocess
import sys
import types
from inspect import signature
from pathlib import Path


def test_host_does_not_export_override_persona_provider():
    import groupmate.host as host
    from groupmate.persona.aemeath import AemeathPersonaProvider

    assert not hasattr(host, "AstrBotPersonaProvider")
    parameters = set(signature(AemeathPersonaProvider).parameters)
    assert "override_prompt" not in parameters
    assert "character_name" not in parameters


def test_status_api_returns_runtime_health_without_removed_config(monkeypatch):
    from groupmate.host.web_api import GroupmateWebAPI

    web = types.ModuleType("astrbot.api.web")
    web.json_response = lambda payload: payload
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
    bridge = types.SimpleNamespace(
        status=lambda: {
            "paused": False,
            "bootstrapped": [],
            "active_persona": "aemeath",
            "config_health": "ok",
        },
    )

    payload = __import__("asyncio").run(GroupmateWebAPI(bridge).status())

    assert payload["active_persona"] == "aemeath"
    assert payload["config_health"] == "ok"
    assert "group_brief" not in repr(payload)
    assert "max_reply_chars" not in repr(payload)


def test_main_loads_via_astrbot_module_path(tmp_path):
    plugin_root = Path(__file__).resolve().parents[1]
    plugins_dir = tmp_path / "data" / "plugins"
    plugin_dir = plugins_dir / "astrbot_plugin_groupmate"
    plugin_dir.mkdir(parents=True)

    for name in ("__init__.py", "main.py"):
        (plugin_dir / name).write_text(
            (plugin_root / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    groupmate_src = plugin_root / "groupmate"
    groupmate_dst = plugin_dir / "groupmate"
    groupmate_dst.mkdir()
    for path in groupmate_src.rglob("*.py"):
        rel = path.relative_to(groupmate_src)
        target = groupmate_dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    script = r'''
import importlib
import sys
import types


def identity_decorator(*args, **kwargs):
    del args, kwargs
    return lambda value: value


class Filter:
    EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="group")
    PlatformAdapterType = types.SimpleNamespace(AIOCQHTTP="aiocqhttp")
    PermissionType = types.SimpleNamespace(ADMIN="admin")
    event_message_type = staticmethod(identity_decorator)
    platform_adapter_type = staticmethod(identity_decorator)
    on_llm_request = staticmethod(identity_decorator)
    permission_type = staticmethod(identity_decorator)
    command = staticmethod(identity_decorator)


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event = types.ModuleType("astrbot.api.event")
star = types.ModuleType("astrbot.api.star")
api.AstrBotConfig = dict
api.logger = types.SimpleNamespace(info=lambda *args: None, exception=lambda *args: None)
event.AstrMessageEvent = object
event.filter = Filter()
star.Context = object
star.Star = object
sys.modules.update({
    "astrbot": astrbot,
    "astrbot.api": api,
    "astrbot.api.event": event,
    "astrbot.api.star": star,
})

data = types.ModuleType("data")
data.__path__ = []
data.__package__ = "data"
plugins = types.ModuleType("data.plugins")
plugins.__path__ = [sys.argv[1]]
plugins.__package__ = "data.plugins"
sys.modules["data"] = data
sys.modules["data.plugins"] = plugins

importlib.import_module("data.plugins.astrbot_plugin_groupmate.main")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(plugins_dir)],
        cwd=str(tmp_path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr
