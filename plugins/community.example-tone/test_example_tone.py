"""
plugin-template契约自检（stdlib unittest）
===================================

对``plugin-template/`` 做三层检查，任何一层红掉都意味着你的插件装不进市场：

1. **manifest 层**：``plugin.json`` 通过 ``sdk.manifest_validator``，
   且 **0 error + 0 warning**（warning 也有价值，见 :func:`test_manifest_zero_warning`）；
2. **契约层**：``entry_point`` 的 ``module:Class`` 可解析，类实现
   ``sdk.contracts.REQUIRED_PLUGIN_METHODS`` 全部 8 个方法；
3. **生命周期层**：``initialize → load → synthesize → synthesize_stream → cancel
   → health → unload → shutdown`` 冒烟跑通，含流式分块与取消路径。

运行方式（**必须**在 ``backend/`` 目录下执行，让 ``sdk`` 可导入）::

    cd backend
    python -m pytest ..\\plugin-template -q -p no:cacheprovider
    # 或
    python ..\\plugin-template\\test_plugin_template.py -v

作者：EasyAI 开发团队
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
import wave
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent
PLUGIN_JSON = TEMPLATE_DIR / "plugin.json"
PLUGIN_MODULE = TEMPLATE_DIR / "example_tone.py"


def _load_plugin_module():
    """按文件路径加载模板模块（不依赖包结构）。"""
    # 先让 SDK 定位引导跑一遍（``sdk`` 可能还不在 sys.path 上）
    spec = importlib.util.spec_from_file_location("example_tone", PLUGIN_MODULE)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载插件模块: {PLUGIN_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# 触发 SDK 定位引导（必须在任何 sdk.* 导入之前）
_load_plugin_module()

from sdk.contracts import REQUIRED_PLUGIN_METHODS  # noqa: E402
from sdk.dto import ModelSpec, PluginConfig, TTSRequest  # noqa: E402
from sdk.enums import EngineState  # noqa: E402
from sdk.exceptions import SynthesisCancelledError  # noqa: E402
from sdk.manifest_validator import validate_manifest_file  # noqa: E402

_MODULE = sys.modules["example_tone"]
_TEMPLATE_CLASS = _MODULE.ExampleTonePlugin
_MODEL_ID = _TEMPLATE_CLASS.DEFAULT_MODEL_ID


class TestManifest(unittest.TestCase):
    """第1 层：manifest 零 error 零 warning。"""

    def setUp(self) -> None:
        self.raw = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))

    def test_manifest_file_exists(self) -> None:
        self.assertTrue(PLUGIN_JSON.is_file(), f"缺少 manifest: {PLUGIN_JSON}")

    def test_manifest_zero_error(self) -> None:
        result = validate_manifest_file(PLUGIN_JSON)
        self.assertTrue(result.ok, f"manifest 校验失败:\n" + "\n".join(result.errors))
        self.assertEqual(result.errors, [], "manifest 必须零 error")
        self.assertIn("[OK]", result.summary())

    def test_manifest_zero_warning(self) -> None:
        """零 warning 的三个充分条件（缺一即失败）。

        校验器只从 3 个来源产出 warning：
          1. ``hardware`` 段缺失；
          2. ``contributes.capabilities`` 含 ``sdk.capabilities.CAPABILITY_FLAGS``
             之外的值；
          3. ``models.discovery == "explicit"`` 但 ``explicit`` 与 ``default`` 都为空。
        """
        result = validate_manifest_file(PLUGIN_JSON)
        self.assertEqual(result.warnings, [], f"manifest 必须零 warning:\n" + "\n".join(result.warnings))

    def test_manifest_plugin_id_contract(self) -> None:
        """``plugin.id`` 必须含点、反向域名风格。"""
        plugin_id = self.raw["plugin"]["id"]
        self.assertGreaterEqual(len(plugin_id), 3)
        self.assertIn(".", plugin_id, "plugin.id 必须含至少一个点")
        self.assertEqual(plugin_id, _TEMPLATE_CLASS.PLUGIN_ID, "plugin.json 与代码的 PLUGIN_ID 必须一致")

    def test_manifest_version_is_strict_semver(self) -> None:
        """``plugin.version`` 严格三段式（``1.2`` 会被拒）。"""
        parts = self.raw["plugin"]["version"].split(".")
        self.assertEqual(len(parts), 3, "version 必须是 X.Y.Z 三段")

    def test_manifest_entry_point_has_colon(self) -> None:
        """``entry_point`` 必须是 ``module:Class``，且 module 是合法 Python 标识符。"""
        entry = self.raw["plugin"]["entry_point"]
        self.assertIn(":", entry)
        module_name, class_name = entry.split(":", 1)
        self.assertTrue(module_name.isidentifier(), f"module 必须是标识符（不能有 -）: {module_name}")
        self.assertTrue(class_name.isidentifier(), f"Class 必须是标识符: {class_name}")
        self.assertEqual(module_name, PLUGIN_MODULE.stem, "module 名必须与文件名一致")

    def test_manifest_capabilities_are_known(self) -> None:
        """``contributes.capabilities`` 全部取值必须在 ``CAPABILITY_FLAGS`` 内。"""
        from sdk.capabilities import CAPABILITY_FLAGS

        for flag in self.raw.get("contributes", {}).get("capabilities", []):
            self.assertIn(flag, CAPABILITY_FLAGS, f"未知能力标识: {flag}")

    def test_manifest_explicit_models_require_name(self) -> None:
        """``models.explicit[]`` 的必填键是 ``name``（**不是** ``model_id``）。"""
        for entry in self.raw.get("models", {}).get("explicit", []):
            self.assertIn("name", entry)
            self.assertNotIn("model_id", entry, "models.explicit[] 用 name，不是 model_id")

    def test_class_manifest_matches_disk(self) -> None:
        """代码里的 ``manifest()`` 与磁盘 ``plugin.json`` 的身份字段一致。"""
        declared = _TEMPLATE_CLASS.manifest()
        self.assertEqual(declared.plugin.id, self.raw["plugin"]["id"])
        self.assertEqual(declared.plugin.version, self.raw["plugin"]["version"])
        self.assertEqual(declared.plugin.entry_point, self.raw["plugin"]["entry_point"])


class TestContract(unittest.TestCase):
    """第 2 层：入口点可解析 + 8 个契约方法齐备。"""

    def test_entry_point_resolvable(self) -> None:
        raw = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        module_name, class_name = raw["plugin"]["entry_point"].split(":", 1)
        self.assertEqual(module_name, PLUGIN_MODULE.stem)
        cls = getattr(_MODULE, class_name)
        self.assertTrue(callable(cls))

    def test_class_is_no_arg_constructible(self) -> None:
        """宿主按 ``Class()`` 无参构造；必填构造参数会让 activate 失败。"""
        instance = _TEMPLATE_CLASS()
        self.addCleanup(lambda: asyncio.run(instance.shutdown()))
        self.assertIsNotNone(instance)

    def test_all_required_methods_present(self) -> None:
        for name in REQUIRED_PLUGIN_METHODS:
            self.assertTrue(hasattr(_TEMPLATE_CLASS, name), f"缺少契约方法: {name}")

    def test_required_methods_are_callable(self) -> None:
        for name in REQUIRED_PLUGIN_METHODS:
            member = getattr(_TEMPLATE_CLASS, name, None)
            self.assertTrue(callable(member), f"契约方法不可调用: {name}")

    def test_synthesize_stream_is_async_generator(self) -> None:
        """``synthesize_stream`` 必须是 async generator（``async def`` + ``yield``）。"""
        import inspect

        method = _TEMPLATE_CLASS.synthesize_stream
        self.assertTrue(
            inspect.isasyncgenfunction(method),
            "synthesize_stream 必须是 'async def' + 'yield' 的 async generator（PEP 525）",
        )


class TestLifecycle(unittest.TestCase):
    """第 3 层：生命周期冒烟（含流式与取消）。"""

    async def _plugin(self, **config_kwargs):
        """构造并完成 initialize + load（**必须**在事件循环内await，不能用 asyncio.run 嵌套）。"""
        plugin = _TEMPLATE_CLASS()
        await plugin.initialize(PluginConfig(device="cpu", **config_kwargs))
        await plugin.load(ModelSpec(model_id=_MODEL_ID))
        return plugin

    # -- 3.1 完整生命周期 --

    def test_full_lifecycle(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            try:
                response = await plugin.synthesize(TTSRequest(text="你好 EasyAI"))
                self.assertTrue(response.is_success)
                self.assertIsNotNone(response.audio_path)
                self.assertGreater(response.duration, 0)

                health = await plugin.health()
                self.assertTrue(health.healthy)
                self.assertIn(health.status, tuple(EngineState))
                self.assertEqual(health.loaded_models, [_MODEL_ID])

                await plugin.unload(_MODEL_ID)
                self.assertEqual((await plugin.health()).loaded_models, [])
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    # -- 3.2 合成产物真的是合法 WAV --

    def test_synthesize_writes_playable_wav(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            try:
                response = await plugin.synthesize(TTSRequest(text="模板自检"))
                path = Path(str(response.audio_path))
                self.assertTrue(path.is_file())
                with wave.open(str(path), "rb") as handle:
                    self.assertEqual(handle.getnchannels(), 1)
                    self.assertEqual(handle.getsampwidth(), 2, "必须是 16bit")
                    self.assertGreater(handle.getnframes(), 0)
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    # -- 3.3 未 load 就 synthesize 必须报错 --

    def test_synthesize_requires_load(self) -> None:
        async def scenario() -> None:
            plugin = _TEMPLATE_CLASS()
            await plugin.initialize(PluginConfig(device="cpu"))
            try:
                with self.assertRaises(Exception):
                    await plugin.synthesize(TTSRequest(text="还没 load"))
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    # -- 3.4 空文本必须被 request.validate() 拦下 --

    def test_empty_text_rejected(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            try:
                with self.assertRaises(Exception):
                    await plugin.synthesize(TTSRequest(text="   "))
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    # -- 3.5 流式：多块 + 末块才带 audio_path --

    def test_stream_yields_multiple_chunks(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            try:
                chunks = []
                async for chunk in plugin.synthesize_stream(
                    TTSRequest(text="流式合成演示一二三四五六七八九十")
                ):
                    chunks.append(chunk)
                self.assertGreater(len(chunks), 1, "流式应产出多块")

                for chunk in chunks[:-1]:
                    self.assertIsNone(chunk.audio_path, "中间块不应带 audio_path")
                last = chunks[-1]
                self.assertIsNotNone(last.audio_path)
                self.assertTrue(last.is_success)
                self.assertTrue(Path(str(last.audio_path)).is_file())
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    # -- 3.6 取消：进行中的流式请求被 cancel 后抛 SynthesisCancelledError --

    def test_cancel_aborts_running_stream(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            request = TTSRequest(text="这是一段会被取消的流式合成请求一二三四五六七八九十")
            try:
                accepted = False
                with self.assertRaises(SynthesisCancelledError):
                    async for _ in plugin.synthesize_stream(request):
                        # 第一次 cancel 在拿到第一块之后立刻受理
                        if not accepted:
                            accepted = await plugin.cancel(request.request_id)
                        self.assertTrue(accepted, "cancel 应当被受理")
                # 取消后活动集合必须已清理（否则下一次 health 会一直显示有活动请求）
                self.assertEqual((await plugin.health()).details.get("active_requests"), [])
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    def test_cancel_unknown_request_returns_false(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            try:
                self.assertFalse(await plugin.cancel("not-a-live-request-id"))
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())

    # -- 3.7 unload / shutdown 幂等 --

    def test_unload_and_shutdown_are_idempotent(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            await plugin.unload(_MODEL_ID)
            await plugin.unload(_MODEL_ID)  # 不存在的 model_id 必须静默成功
            await plugin.unload("never-loaded-at-all")
            await plugin.shutdown()
            await plugin.shutdown()  # 重复 shutdown 必须安全
            self.assertEqual((await plugin.health()).status, EngineState.SHUTDOWN)

        asyncio.run(scenario())

    # -- 3.8 health 的status 不得出现 "healthy" --

    def test_health_status_never_healthy_enum(self) -> None:
        async def scenario() -> None:
            plugin = await self._plugin()
            try:
                health = await plugin.health()
                self.assertNotEqual(health.status, "healthy", "EngineState 没有 healthy 取值")
                self.assertIsInstance(health.healthy, bool)
                self.assertEqual(health.details.get("status_text"), "healthy")
            finally:
                await plugin.shutdown()

        asyncio.run(scenario())


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)