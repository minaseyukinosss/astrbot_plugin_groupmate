"""AstrBot loads plugins below the ``data.plugins`` package namespace."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]


def test_plugin_imports_from_the_astrbot_data_plugins_namespace(tmp_path: Path):
    plugins = tmp_path / "data" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "astrbot_plugin_groupmate").symlink_to(
        ROOT,
        target_is_directory=True,
    )
    script = textwrap.dedent(
        f"""
        import asyncio
        import sys
        import types

        sys.path.insert(0, {str(tmp_path)!r})

        astrbot = types.ModuleType("astrbot")
        astrbot.__path__ = []
        api = types.ModuleType("astrbot.api")
        api.__path__ = []
        event = types.ModuleType("astrbot.api.event")
        star = types.ModuleType("astrbot.api.star")
        core = types.ModuleType("astrbot.core")
        core.__path__ = []
        utils = types.ModuleType("astrbot.core.utils")
        utils.__path__ = []
        astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")

        class AstrMessageEvent:
            pass

        class EventMessageType:
            GROUP_MESSAGE = "group_message"

        class Filter:
            EventMessageType = EventMessageType

            @staticmethod
            def event_message_type(*_args, **_kwargs):
                return lambda handler: handler

        class Context:
            def register_web_api(self, *_args, **_kwargs):
                pass

        class Star:
            def __init__(self, context):
                self.context = context

        plugin_data = {str(tmp_path / "astrbot-data" / "plugin_data")!r}
        astrbot_path.get_astrbot_plugin_data_path = lambda: plugin_data

        event.AstrMessageEvent = AstrMessageEvent
        event.filter = Filter()
        star.Context = Context
        star.Star = Star
        sys.modules.update({{
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.core": core,
            "astrbot.core.utils": utils,
            "astrbot.core.utils.astrbot_path": astrbot_path,
        }})

        module = __import__(
            "data.plugins.astrbot_plugin_groupmate.main",
            fromlist=["main"],
        )
        assert module.GroupmatePlugin.__module__ == (
            "data.plugins.astrbot_plugin_groupmate.main"
        )
        plugin = module.GroupmatePlugin(Context(), {{}})
        assert plugin.data_dir == (
            __import__("pathlib").Path(plugin_data) / "astrbot_plugin_groupmate"
        )

        async def initialize_without_group_scope():
            unscoped = module.GroupmatePlugin(
                Context(),
                {{"runtime_mode": "SHADOW", "enabled_groups": []}},
            )
            await unscoped.initialize()
            assert unscoped.bridge._manager is None
            assert unscoped._control_api is None
            await unscoped.terminate()

        asyncio.run(initialize_without_group_scope())
        """
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
