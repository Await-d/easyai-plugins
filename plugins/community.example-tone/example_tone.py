"""
EasyAI 引擎插件模板 · 最小可运行实现
====================================

复制本文件 + 同目录 ``plugin.json`` 即可得到一个能装进 EasyAI 插件市场的引擎插件。
本文档与代码的唯一真相源在同目录 ``README.md``（"你要改的 4 处"）。

刻意保持的三个性质
------------------
1. **零第三方依赖**：只用标准库（``array`` / ``asyncio`` / ``math`` / ``shutil`` /
   ``sys`` / ``tempfile`` / ``time`` / ``uuid`` / ``wave`` / ``pathlib``）。因此
   ``isolation.mode`` 可以安全写 ``in_process``，不需要独立 venv、不需要任何 wheel。
2. **无参可构造**：``__init__(self)`` 不接收任何参数。宿主按 ``entry_point``
   ``ExampleTonePlugin()`` 直接无参构造，任何必填构造参数都会让 ``activate`` 失败。
3. **模块名全局唯一**：见 ``README.md``「模块名必须全局唯一」一节。三个内置插件
   最初都叫 ``adapter.py``，``sys.modules`` 按模块名缓存，导致后加载的插件拿到了
   前一个插件的类。

SDK 定位引导
------------
宿主会把插件目录加入 ``sys.path`` 后import 本模块，因此正常运行路径不需要这段引导。
但为了能直接 ``python plugin-template/example_tone.py`` 当脚本调试，
这里做一次**幂等**的向上查找 —— **刻意不写死任何绝对路径**，换机器或换检出位置也能跑：

    1. 先试 ``import sdk``，能导入就直接返回（宿主路径）；
    2. 否则从本文件逐级向上，先找 ``<parent>/sdk/__init__.py``（SDK 与本文件同级布局），
       再找 ``<parent>/backend/sdk/__init__.py``（EasyAI 仓库布局：模板在项目根、
       SDK 在 ``backend/sdk``）；
    3. 命中则 ``sys.path.insert``；全都没命中才抛 ``ImportError``。

作者：EasyAI 开发团队（模板，可自由修改/分发）
"""

from __future__ import annotations

import array
import asyncio
import math
import shutil
import sys
import tempfile
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator


# =============================================================================
# 1) SDK 定位引导（必须先于任何 sdk.* 导入执行，顺序不可调整）
# =============================================================================
def _ensure_sdk_on_path() -> None:
    """把包含 ``sdk`` 包的目录加入 ``sys.path``（幂等，不写死绝对路径）。"""
    try:
        import sdk  # noqa: F401

        return
    except ImportError:
        pass

    for parent in Path(__file__).resolve().parents:
        for candidate in (parent / "sdk", parent / "backend" / "sdk"):
            if (candidate / "__init__.py").is_file():
                directory = str(candidate.parent)
                if directory not in sys.path:
                    sys.path.insert(0, directory)
                return
    raise ImportError(
        "未找到 easyai-engine-sdk（sdk/__init__.py）："
        "请把 backend/ 加入 PYTHONPATH，或先安装 SDK 包"
    )


_ensure_sdk_on_path()

# 引导完成后才能导入 SDK 契约
from sdk.capabilities import EngineCapabilities  # noqa: E402
from sdk.contracts import IEnginePlugin, PluginManifest  # noqa: E402
from sdk.dto import (  # noqa: E402
    HealthStatus,
    InitResult,
    LoadResult,
    ModelSpec,
    PluginConfig,
    TTSRequest,
    TTSResponse,
)
from sdk.enums import EngineKind, EngineState, TaskStatus  # noqa: E402
from sdk.exceptions import (  # noqa: E402
    ConfigurationError,
    EngineLoadError,
    SDKError,
    SynthesisCancelledError,
    SynthesisError,
)
from sdk.version import SDK_API_VERSION  # noqa: E402

__all__ = ["ExampleTonePlugin"]


# =============================================================================
# 2) 模板常量（把"你的引擎参数"集中放这里）
# =============================================================================
#: 默认采样率（Hz）。实际以 ``VoiceParams.sample_rate`` 为准，会被夹紧。
SAMPLE_RATE = 22050
#: 声道数（单声道）。
CHANNELS = 1
#: 采样字节宽度（2 = 16bit）。WAV 的 PCM 16bit 是小端。
SAMPLE_WIDTH = 2
#: 基频（Hz，C4）。
BASE_FREQ_HZ = 261.63
#: 大调五声音阶比例（避免明显不谐和音程）。
SCALE_RATIO = (1.0, 9 / 8, 5 / 4, 4 / 3, 3 / 2, 5 / 3, 15 / 8, 2.0)
#: 谐波（倍数, 权重）；权重和即归一化分母。
HARMONICS = ((1, 1.0), (2, 0.32), (3, 0.14))
#: 音量倍率 1.0 时的峰值振幅。
PEAK_AMPLITUDE = 0.32
#: 每个字符的基础音节时长（秒）。
SECONDS_PER_CHAR = 0.18
#: 单次合成时长下限 / 上限（秒）。
MIN_DURATION_S = 0.5
MAX_DURATION_S = 15.0
#: 采样率夹紧范围。
MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 48000
#: 流式分块的目标时长（秒）与最少/最多块数。
CHUNK_SECONDS = 0.6
MIN_STREAM_CHUNKS = 4
MAX_STREAM_CHUNKS = 8
#: 视为停顿的字符（渲染为短静音）。
REST_CHARS = frozenset(" \t\r\n，。、；：！？,.!?;:…—-～~()（）《》【】\"'“”‘’")


@dataclass(frozen=True, slots=True)
class _Syllable:
    """一个「音节」的渲染参数（本模板的最小合成单元）。"""

    frames: int
    freq_hz: float
    amplitude: float


# =============================================================================
# 3) 插件实现
# =============================================================================
class ExampleTonePlugin(IEnginePlugin):
    """最小可运行的 TTS 引擎模板。

    它不学任何声学模型，而是用**五声音阶 + 谐波叠加 + 起落包络**把每个字符映射成
    一个音节，因此输出的 WAV 是**真实波形**（非静音），可直接试听。

    生命周期（宿主侧驱动）::

        plugin = ExampleTonePlugin()                      # 无参构造
        await plugin.initialize(PluginConfig(device="cpu"))
        await plugin.load(ModelSpec(model_id="template-tone-v1"))
        resp = await plugin.synthesize(TTSRequest(text="你好 EasyAI"))
        async for chunk in plugin.synthesize_stream(TTSRequest(text="流式")):
            ...
        await plugin.health()
        await plugin.unload("template-tone-v1")
        await plugin.shutdown()                            # 幂等

    Examples:
        >>> import asyncio
        >>> from sdk import ModelSpec, PluginConfig, TTSRequest
        >>> async def demo():
        ...     engine = ExampleTonePlugin()
        ...     await engine.initialize(PluginConfig(device="cpu"))
        ...     await engine.load(ModelSpec(model_id="template-tone-v1"))
        ...     resp = await engine.synthesize(TTSRequest(text="你好"))
        ...     return resp.is_success
        >>> asyncio.run(demo())
        True
    """

    #: 反向域名插件 ID；须与 plugin.json 的 plugin.id **保持一致**。
    PLUGIN_ID = "community.example-tone"

    #: 模板自带的演示模型 ID（须与 plugin.json 的 models.default 一致）。
    DEFAULT_MODEL_ID = "template-tone-v1"

    def __init__(self) -> None:
        """无参构造 —— 宿主按 ``entry_point`` 直接 ``ExampleTonePlugin()``。

        **不要**在这里加必填参数：宿主不会传任何东西，加了必填参数会导致
        ``activate`` 阶段构造失败（422）。调试开关请做成类属性或只读环境变量。
        """
        self._state: EngineState = EngineState.CREATED
        self._config: PluginConfig | None = None
        self._session_id: str = uuid.uuid4().hex[:12]
        self._models: dict[str, ModelSpec] = {}
        self._active: set[str] = set()
        self._cancelled: set[str] = set()
        self._started_at: float | None = None
        self._output_dir: Path | None = None
        self._owns_output_dir: bool = False
        self._artifacts: set[Path] = set()

    # ------------------------------------------------------------------ #
    # manifest（与 plugin.json 同构；宿主以磁盘上的 plugin.json 为准）
    # ------------------------------------------------------------------ #
    @classmethod
    def manifest(cls) -> PluginManifest:
        """返回与 ``plugin.json`` **同构**的 manifest。

        脱离宿主时插件靠它自描述（自检、单测都走这里）。两边必须同步修改，
        否则市场展示的能力会与实际能力漂移。
        """
        from sdk.contracts import (
            ContributesSpec,
            DependenciesSpec,
            HardwareRequirement,
            HardwareSpec,
            IsolationSpec,
            ModelEntry,
            ModelsSpec,
            PermissionsSpec,
            PluginInfo,
        )
        from sdk.enums import HardwareBackend, IsolationMode

        return PluginManifest(
            plugin=PluginInfo(
                id=cls.PLUGIN_ID,
                name="Community Example Tone（社区示例引擎）",
                version="0.1.0",
                engine_kind=EngineKind.TTS,
                api_version=">=1.0,<2.0",
                entry_point="example_tone:ExampleTonePlugin",
                license="MIT",
                authors=["你的名字 <you@example.com>"],
                description="零第三方依赖的最小 TTS 插件模板",
            ),
            isolation=IsolationSpec(mode=IsolationMode.IN_PROCESS, python=">=3.10"),
            hardware=HardwareSpec(
                requires=HardwareRequirement(backend=[HardwareBackend.CPU], min_vram_gb=0),
                recommends=HardwareRequirement(backend=[HardwareBackend.CPU], min_vram_gb=0),
                cpu_fallback=True,
            ),
            dependencies=DependenciesSpec(python_packages=[]),
            permissions=PermissionsSpec(filesystem=["models", "cache"], network=[], gpu=False),
            models=ModelsSpec(
                discovery="explicit",
                default=cls.DEFAULT_MODEL_ID,
                explicit=[ModelEntry(name=cls.DEFAULT_MODEL_ID)],
            ),
            contributes=ContributesSpec(
                capabilities=["streaming", "cancel", "speed_control", "pitch_control", "volume_control"],
                params_schema={
                    "type": "object",
                    "properties": {
                        "speed": {"type": "number", "minimum": 0.25, "maximum": 4.0, "default": 1.0},
                        "pitch": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                        "volume": {"type": "number", "minimum": 0.0, "maximum": 2.0, "default": 1.0},
                    },
                },
                ui={"icon": None, "category": "模板/演示", "tags": ["零依赖", "可离线", "教学模板"]},
            ),
        )

    # ------------------------------------------------------------------ #
    # 1/8  initialize
    # ------------------------------------------------------------------ #
    async def initialize(self, config: PluginConfig) -> InitResult:
        """初始化运行环境、准备输出目录并声明能力。

        输出目录优先级：``config.workspace_dir`` → ``config.cache_dir`` →
        插件自建的系统临时目录（自建目录在 :meth:`shutdown` 时清理）。

        Raises:
            ConfigurationError: ``max_concurrent`` 非法。
            EngineInitError: 输出目录不可创建。
        """
        if config.max_concurrent < 1:
            raise ConfigurationError(
                "max_concurrent 必须 >= 1",
                error_code="INVALID_MAX_CONCURRENT",
                details={"max_concurrent": config.max_concurrent},
            )

        self._config = config
        self._state = EngineState.INITIALIZING
        self._output_dir, self._owns_output_dir = self._prepare_output_dir(config)
        self._started_at = time.time()
        self._state = EngineState.INITIALIZED

        # 能力声明必须与 plugin.json 的 contributes.capabilities 一致
        capabilities = EngineCapabilities(
            engine_kind=EngineKind.TTS,
            supports_streaming=True,
            supports_cancel=True,
            supports_speed_control=True,
            supports_pitch_control=True,
            supports_volume_control=True,
            supported_languages=["zh", "en"],
            supported_formats=["wav"],
            quality_levels=["draft", "standard", "high"],
        )
        capabilities.validate()  # 契约要求声明是自洽的

        return InitResult(
            ok=True,
            capabilities=capabilities,
            sdk_version=SDK_API_VERSION,  # 宿主据此做版本握手
            plugin_id=self.PLUGIN_ID,
            details={
                "device": self._resolve_device(),
                "session_id": self._session_id,
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "output_dir": str(self._output_dir),
                "owns_output_dir": self._owns_output_dir,
            },
        )

    # ------------------------------------------------------------------ #
    # 2/8  load
    # ------------------------------------------------------------------ #
    async def load(self, model_spec: ModelSpec) -> LoadResult:
        """加载（预热）"模型"。

        本模板没有神经网络权重，「模型」是一张音调表；加载动作是**记录 + 预计算**，
        目的是让 ``health().loaded_models`` 与 ``unload(model_id)`` 有真实语义。

        Raises:
            EngineLoadError: ``model_id`` 为空 / 声明了本地路径但路径不存在。
            SDKError: 尚未 :meth:`initialize`。
        """
        self._require_state(EngineState.INITIALIZED, EngineState.READY, action="load")

        if not (model_spec.model_id or "").strip():
            raise EngineLoadError(
                "model_id 不能为空",
                error_code="EMPTY_MODEL_ID",
                details={"model_spec": model_spec.to_dict()},
            )
        if model_spec.path and not Path(model_spec.path).exists():
            raise EngineLoadError(
                f"模型路径不存在: {model_spec.path}",
                error_code="MODEL_PATH_NOT_FOUND",
                details={"model_id": model_spec.model_id, "path": model_spec.path},
            )

        self._state = EngineState.LOADING
        started = time.perf_counter()
        try:
            await asyncio.sleep(0)  # 让出事件循环，模拟异步 IO
            # 真实插件在这里把权重读进显存/内存
            self._models[model_spec.model_id] = model_spec
        except OSError as exc:  # pragma: no cover - 本模板是纯内存操作
            self._state = EngineState.ERROR
            raise EngineLoadError(
                f"预热失败: {exc}",
                error_code="WARMUP_FAILED",
                details={"model_id": model_spec.model_id},
            ) from exc

        self._state = EngineState.READY
        return LoadResult(
            ok=True,
            model_id=model_spec.model_id,
            device=model_spec.device or self._resolve_device(),
            vram_used_mb=0.0,  # 纯 CPU，不占显存
            message="模板插件：无权重加载，仅登记模型",
            details={"warmup_ms": round((time.perf_counter() - started) * 1000, 3)},
        )

    # ------------------------------------------------------------------ #
    # 3/8  unload（必须幂等）
    # ------------------------------------------------------------------ #
    async def unload(self, model_id: str) -> None:
        """卸载指定模型；对不存在的 ``model_id`` **静默成功**（幂等要求）。"""
        self._models.pop(model_id, None)
        if not self._models and self._state in (EngineState.READY, EngineState.BUSY):
            self._state = EngineState.INITIALIZED
        await asyncio.sleep(0)

    # ------------------------------------------------------------------ #
    # 4/8  synthesize
    # ------------------------------------------------------------------ #
    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """一次性合成整段 WAV 并落盘。

        流程：``request.validate()`` → 状态/模型校验 → 规划音节 → 写 WAV →
        回报 ``TTSResponse``。**音频只走 ``audio_path``，绝不塞 base64 进 JSON**
        （base64 会膨胀 33%）。

        Raises:
            ConfigurationError: 文本为空或语音参数越界（来自 ``request.validate()``）。
            SynthesisError: 模型未加载 / 输出格式不支持。
            SynthesisCancelledError: 请求在合成过程中被 :meth:`cancel` 取消。
            SDKError: 插件尚未 ``load``，状态不允许合成。
        """
        request.validate()  # 第一件事：交给 SDK 做契约级校验
        self._require_state(EngineState.READY, EngineState.BUSY, action="synthesize")
        model_id = self._resolve_model_id(request)
        sample_rate = self._resolve_sample_rate(request.voice)

        started = time.perf_counter()
        self._active.add(request.request_id)
        self._state = EngineState.BUSY
        out_path: Path | None = None
        try:
            await asyncio.sleep(0)  # 让出事件循环，保证可被 cancel 打断
            self._raise_if_cancelled(request.request_id)

            syllables = self._plan_syllables(request, sample_rate)
            out_path = self._resolve_output_path(request)
            frames = self._write_wav(out_path, syllables, sample_rate)
            self._raise_if_cancelled(request.request_id)

            return TTSResponse.completed(
                request.request_id,
                audio_path=str(out_path),
                duration=round(frames / sample_rate, 3),
                sample_rate=sample_rate,
                channels=CHANNELS,
                file_size=out_path.stat().st_size,
                output_format="wav",
                model_id=model_id,
                processing_time=round(time.perf_counter() - started, 4),
                metadata=self._build_metadata(request, frames),
            )
        except SynthesisCancelledError:
            self._discard_output(out_path)  # 半成品对宿主无意义，直接清掉
            raise
        finally:
            self._active.discard(request.request_id)
            self._state = EngineState.READY if self._models else EngineState.INITIALIZED

    # ------------------------------------------------------------------ #
    # 5/8  synthesize_stream（PEP 525 async generator）
    # ------------------------------------------------------------------ #
    async def synthesize_stream(self, request: TTSRequest) -> AsyncIterator[TTSResponse]:
        """流式合成：逐块写入同一个 WAV 文件并逐块通知。

        写法要点（写不对就拿不到流式/取消能力）：

        - 必须是 ``async def`` + ``yield`` 的 **async generator**；接口层级标注的是
          ``def ... -> AsyncIterator``（不是 ``async def``），调用方用 ``async for`` 消费；
        - 每个分块之间 ``await asyncio.sleep(0)`` **让出事件循环**，宿主的 ``cancel()``
          才能被及时处理——中间没有 await 点的生成器是无法被取消的；
        - 中间块 ``status=RUNNING`` 且**不带** ``audio_path``；末块 ``status=COMPLETED``
          并携带完整的 ``audio_path`` / ``file_size``；
        - ``finally`` 里必须关句柄 + 清状态；被取消或消费者提前 ``break`` 时删除半成品。

        Raises:
            ConfigurationError: 请求非法（来自 ``request.validate()``）。
            SynthesisError: 模型未加载 / 输出格式不支持。
        """
        request.validate()
        self._require_state(EngineState.READY, EngineState.BUSY, action="synthesize_stream")
        model_id = self._resolve_model_id(request)
        sample_rate = self._resolve_sample_rate(request.voice)

        syllables = self._plan_syllables(request, sample_rate)
        buckets = self._bucket_syllables(syllables, sample_rate)
        total_frames = sum(syl.frames for syl in syllables)
        out_path = self._resolve_output_path(request, prefix="template-stream")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        handle: wave.Wave_write | None = None
        completed = False
        self._active.add(request.request_id)
        self._state = EngineState.BUSY
        try:
            handle = wave.open(str(out_path), "wb")
            handle.setnchannels(CHANNELS)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(sample_rate)

            written = 0
            for index, bucket in enumerate(buckets):
                # 让出事件循环 → 宿主此刻的 cancel() 才能被投递进来
                await asyncio.sleep(0)
                self._raise_if_cancelled(request.request_id)

                chunk_frames = sum(syl.frames for syl in bucket)
                handle.writeframes(self._render(bucket, sample_rate))
                written += chunk_frames

                is_last = index == len(buckets) - 1
                if is_last:
                    handle.close()  # 关闭时 wave 模块回填 WAV 头
                    handle = None
                    self._remember_artifact(out_path)

                yield TTSResponse(
                    request_id=request.request_id,
                    status=TaskStatus.COMPLETED if is_last else TaskStatus.RUNNING,
                    model_id=model_id,
                    audio_path=str(out_path) if is_last else None,
                    duration=round(chunk_frames / sample_rate, 3),
                    sample_rate=sample_rate,
                    channels=CHANNELS,
                    file_size=out_path.stat().st_size if is_last else None,
                    output_format="wav" if is_last else None,
                    metadata={
                        **self._build_metadata(request, total_frames),
                        "chunk_index": index,
                        "chunk_count": len(buckets),
                        "is_last": is_last,
                        "frames_written": written,
                        "total_frames": total_frames,
                    },
                )
                if is_last:
                    completed = True
        finally:
            # 无论正常结束 / 被取消 / 消费者提前 break，都会走到这里
            if handle is not None:
                handle.close()
            self._active.discard(request.request_id)
            self._state = EngineState.READY if self._models else EngineState.INITIALIZED
            if not completed:
                self._discard_output(out_path)

    # ------------------------------------------------------------------ #
    # 6/8  cancel
    # ------------------------------------------------------------------ #
    async def cancel(self, request_id: str) -> bool:
        """请求取消进行中的合成。

        ``synthesize`` / ``synthesize_stream`` 在下一个检查点检测到标记即抛
        :class:`SynthesisCancelledError`。

        Returns:
            ``True`` 表示已受理（``request_id`` 处于活动集合中）；请求不存在或已完成时
            返回 ``False``。
        """
        if request_id in self._active:
            self._cancelled.add(request_id)
            return True
        return False

    # ------------------------------------------------------------------ #
    # 7/8  health
    # ------------------------------------------------------------------ #
    async def health(self) -> HealthStatus:
        """返回健康与资源状态。

        ⚠️ 常见坑：``HealthStatus.status`` 的类型是 :class:`sdk.enums.EngineState`，
        取值只有 ``created / initializing / initialized / loading / ready / busy /
        unloading / error / shutdown`` —— **没有** ``"healthy"`` 这个取值。
        「健康」用 ``healthy=True`` 表达，``status`` 仍是真实状态；额外在
        ``details.status_text`` 里给出 ``"healthy"`` 便于前端直接展示。
        """
        healthy = self._state in (EngineState.READY, EngineState.BUSY)
        uptime = (time.time() - self._started_at) if self._started_at else 0.0
        message = None
        if self._state == EngineState.SHUTDOWN:
            message = "插件已关闭"
        elif not self._models and self._state == EngineState.INITIALIZED:
            message = "尚未加载模型（load 后方可合成）"

        return HealthStatus(
            status=self._state,
            healthy=healthy,
            vram_used_mb=0.0,
            uptime_s=round(uptime, 3),
            loaded_models=sorted(self._models),
            message=message,
            details={
                "status_text": "healthy" if healthy else self._state.value,
                "session_id": self._session_id,
                "active_requests": sorted(self._active),
                "artifact_count": len(self._artifacts),
            },
        )

    # ------------------------------------------------------------------ #
    # 8/8  shutdown（必须幂等、可重复调用）
    # ------------------------------------------------------------------ #
    async def shutdown(self) -> None:
        """释放全部资源。

        仅当输出目录是插件**自建**的临时目录时才整目录删除；若目录由宿主通过
        ``workspace_dir`` / ``cache_dir`` 提供，则只清理本次运行产生的文件，
        不越界删除宿主的其他内容。重复调用安全。
        """
        if self._owns_output_dir and self._output_dir is not None:
            shutil.rmtree(self._output_dir, ignore_errors=True)
        else:
            self.cleanup_artifacts()

        self._artifacts.clear()
        self._models.clear()
        self._active.clear()
        self._cancelled.clear()
        self._state = EngineState.SHUTDOWN
        await asyncio.sleep(0)

    # ------------------------------------------------------------------ #
    # 公开辅助
    # ------------------------------------------------------------------ #
    def cleanup_artifacts(self) -> int:
        """删除本次运行产出的音频文件，返回删除数量。

        宿主如需长期保留音频（例如导出给用户），应在读取完成后**不要**调用本方法。
        """
        removed = 0
        for path in list(self._artifacts):
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - 删除失败不应影响主流程
                pass
            self._artifacts.discard(path)
        return removed

    # ------------------------------------------------------------------ #
    # 内部辅助 · 环境
    # ------------------------------------------------------------------ #
    def _prepare_output_dir(self, config: PluginConfig) -> tuple[Path, bool]:
        """决定输出目录，返回 ``(目录, 是否自建)``。

        Raises:
            EngineInitError: 目录不可创建。
        """
        if config.workspace_dir:
            base, owned = Path(config.workspace_dir), False
        elif config.cache_dir:
            base, owned = Path(config.cache_dir), False
        else:
            base = Path(tempfile.mkdtemp(prefix=f"easyai-{self.PLUGIN_ID}-"))
            owned = True
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SDKError(
                f"无法创建输出目录: {base} ({exc})",
                error_code="OUTPUT_DIR_UNAVAILABLE",
                details={"workspace_dir": config.workspace_dir, "cache_dir": config.cache_dir},
            ) from exc
        return base, owned

    def _resolve_device(self) -> str:
        """把宿主下发的 ``device`` 归一化（本模板恒为 CPU）。"""
        device = (self._config.device if self._config else "auto") or "auto"
        return "cpu" if device == "auto" else device

    def _resolve_sample_rate(self, request_voice: Any) -> int:
        """夹紧采样率到合理区间，避免宿主传入极端值导致超大文件。"""
        return int(
            min(max(int(request_voice.sample_rate), MIN_SAMPLE_RATE), MAX_SAMPLE_RATE)
        )

    def _require_state(self, *allowed: EngineState, action: str) -> None:
        """断言当前状态允许某动作，否则抛 ``SDKError(ENGINE_NOT_READY)``。"""
        if self._state not in allowed:
            raise SDKError(
                f"插件当前状态 {self._state.value} 不允许执行 {action}",
                error_code="ENGINE_NOT_READY",
                details={
                    "state": self._state.value,
                    "action": action,
                    "allowed": [item.value for item in allowed],
                },
            )

    def _raise_if_cancelled(self, request_id: str) -> None:
        """若请求已被取消，抛 ``SynthesisCancelledError`` 并清理标记。"""
        if request_id in self._cancelled:
            self._cancelled.discard(request_id)
            raise SynthesisCancelledError(
                f"合成请求已取消: {request_id}",
                error_code="SYNTHESIS_CANCELLED",
                details={"request_id": request_id},
            )

    # ------------------------------------------------------------------ #
    # 内部辅助 · 模型
    # ------------------------------------------------------------------ #
    def _resolve_model_id(self, request: TTSRequest) -> str:
        """确定本次请求使用的模型 ID。

        Raises:
            SynthesisError: 指定了未加载的模型，或一个模型都没加载。
        """
        if request.model_id:
            if request.model_id not in self._models:
                raise SynthesisError(
                    f"模型未加载: {request.model_id}",
                    error_code="MODEL_NOT_LOADED",
                    details={
                        "model_id": request.model_id,
                        "loaded_models": sorted(self._models),
                    },
                )
            return request.model_id
        if not self._models:
            raise SynthesisError(
                "尚未加载任何模型，请先调用 load()",
                error_code="NO_MODEL_LOADED",
                details={"request_id": request.request_id},
            )
        return next(iter(self._models))

    # ------------------------------------------------------------------ #
    # 内部辅助 · 音节规划
    # ------------------------------------------------------------------ #
    def _plan_syllables(self, request: TTSRequest, sample_rate: int) -> list[_Syllable]:
        """把文本规划成一串音节（把「你的声学模型」换在这里）。"""
        voice = request.voice
        sample_rate = max(int(sample_rate), 1)
        text = request.text[: max(int(MAX_DURATION_S * sample_rate), 1)]
        count = max(len(text), 1)

        per_char = SECONDS_PER_CHAR / max(float(voice.speed), 0.1)
        duration = min(max(count * per_char, MIN_DURATION_S), MAX_DURATION_S)
        per_char = duration / count

        base_freq = BASE_FREQ_HZ
        amplitude = PEAK_AMPLITUDE * min(max(float(voice.volume), 0.0), 2.0)
        nyquist_limit = sample_rate * 0.45

        syllables: list[_Syllable] = []
        for index, char in enumerate(text):
            frames = max(int(per_char * sample_rate), 1)
            if char in REST_CHARS:
                syllables.append(_Syllable(frames=max(frames // 3, 1), freq_hz=0.0, amplitude=0.0))
                continue
            ratio = SCALE_RATIO[ord(char) % len(SCALE_RATIO)]
            drift = 1.0 + 0.06 * math.sin(2.0 * math.pi * index / count)
            freq = min(base_freq * ratio * drift * max(float(voice.pitch), 0.01), nyquist_limit)
            syllables.append(_Syllable(frames=frames, freq_hz=freq, amplitude=amplitude))

        if not syllables:  # pragma: no cover - request.validate() 已保证文本非空
            syllables.append(_Syllable(frames=max(sample_rate // 2, 1), freq_hz=0.0, amplitude=0.0))
        return syllables

    def _bucket_syllables(self, syllables: list[_Syllable], sample_rate: int) -> list[list[_Syllable]]:
        """把音节按帧数尽量均匀地分桶，作为流式分块。"""
        if len(syllables) <= 1:
            return [list(syllables)]

        total_frames = sum(syl.frames for syl in syllables)
        desired = int(round(total_frames / max(1, int(CHUNK_SECONDS * sample_rate))))
        target = max(MIN_STREAM_CHUNKS, min(MAX_STREAM_CHUNKS, desired))
        target = min(target, len(syllables))
        per_bucket = total_frames / target

        buckets: list[list[_Syllable]] = []
        current: list[_Syllable] = []
        accumulated = 0.0
        for syllable in syllables:
            if current and len(buckets) < target - 1 and accumulated >= per_bucket:
                buckets.append(current)
                current = []
                accumulated = 0.0
            current.append(syllable)
            accumulated += syllable.frames
        if current:
            buckets.append(current)
        return buckets

    # ------------------------------------------------------------------ #
    # 内部辅助 · 音频落盘
    # ------------------------------------------------------------------ #
    def _resolve_output_path(self, request: TTSRequest, *, prefix: str = "template-tts") -> Path:
        """决定输出文件路径：优先 ``request.output_path``，否则落在输出目录。

        Raises:
            SynthesisError: 宿主指定了本模板不支持的输出格式。
        """
        if request.output_format and request.output_format.lower() != "wav":
            raise SynthesisError(
                f"本插件只输出 wav，收到: {request.output_format}",
                error_code="UNSUPPORTED_OUTPUT_FORMAT",
                details={"output_format": request.output_format, "supported": ["wav"]},
            )

        if request.output_path:
            path = Path(request.output_path)
            if path.suffix.lower() != ".wav":
                raise SynthesisError(
                    f"output_path 必须是 .wav 文件: {path}",
                    error_code="UNSUPPORTED_OUTPUT_FORMAT",
                    details={"output_path": str(path)},
                )
            return path

        base = self._output_dir or Path(tempfile.gettempdir())
        safe_id = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in request.request_id)
        return base / f"{prefix}-{safe_id}.wav"

    def _write_wav(self, path: Path, syllables: list[_Syllable], sample_rate: int) -> int:
        """把全部音节一次性写入 WAV，返回总帧数。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(CHANNELS)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(sample_rate)
            handle.writeframes(self._render(syllables, sample_rate))
        self._remember_artifact(path)
        return sum(syl.frames for syl in syllables)

    @staticmethod
    def _render(syllables: list[_Syllable], sample_rate: int) -> bytes:
        """渲染音节序列为 **16bit 小端** PCM 字节串。

        ⚠️ 大端机（``sys.byteorder != "little"``）必须 ``byteswap()``：WAV 的 PCM
        16bit 格式固定小端，不换字节序写出来的文件在任何播放器上都是噪音。
        """
        buffer = array.array("h")
        norm = sum(weight for _, weight in HARMONICS)
        for syllable in syllables:
            frames = max(int(syllable.frames), 1)
            if syllable.amplitude <= 0.0 or syllable.freq_hz <= 0.0:
                buffer.extend([0] * frames)
                continue

            step = 2.0 * math.pi * syllable.freq_hz / sample_rate
            attack = max(1, int(frames * 0.15))
            release = max(1, int(frames * 0.25))
            for index in range(frames):
                if index < attack:
                    envelope = index / attack
                elif index >= frames - release:
                    envelope = max(0.0, (frames - 1 - index) / release)
                else:
                    envelope = 1.0

                value = 0.0
                for multiple, weight in HARMONICS:
                    value += weight * math.sin(step * multiple * index)
                sample = int(round(32767.0 * syllable.amplitude * envelope * value / norm))
                buffer.append(-32768 if sample < -32768 else (32767 if sample > 32767 else sample))

        if sys.byteorder != "little":
            buffer.byteswap()  # WAV 固定小端
        return buffer.tobytes()

    def _remember_artifact(self, path: Path) -> None:
        """登记本次产出的文件（仅登记落在插件自己目录内的文件）。"""
        if self._output_dir is not None:
            try:
                path.relative_to(self._output_dir)
            except ValueError:
                return  # 宿主指定的输出路径，不归本插件清理
        self._artifacts.add(path)

    def _discard_output(self, path: Path | None) -> None:
        """删除半成品 / 已登记的输出文件（失败仅忽略）。"""
        if path is None:
            return
        self._artifacts.discard(path)
        try:
            if path.is_file():
                path.unlink()
        except OSError:  # pragma: no cover
            pass

    def _build_metadata(self, request: TTSRequest, frames: int) -> dict[str, Any]:
        """构造回带的元数据（宿主原样透传的 ``request.metadata`` 会合并进来）。"""
        return {
            **dict(request.metadata),
            "engine": "easyai-template-tts",
            "session_id": self._session_id,
            "frames": frames,
            "speed": request.voice.speed,
            "pitch": request.voice.pitch,
            "volume": request.voice.volume,
        }


# =============================================================================
# 直接运行时的自检：python plugin-template/example_tone.py
# =============================================================================
async def _self_check() -> None:  # pragma: no cover - 手工冒烟入口
    """无宿主场景下的最小自检：initialize → load → synthesize → health → shutdown。"""
    plugin = ExampleTonePlugin()
    await plugin.initialize(PluginConfig(device="cpu", workspace_dir=None))
    await plugin.load(ModelSpec(model_id=ExampleTonePlugin.DEFAULT_MODEL_ID))

    response = await plugin.synthesize(TTSRequest(text="你好 EasyAI 插件模板"))
    print(f"synthesize success={response.is_success}")
    print(f"audio_path={response.audio_path}")
    print(f"duration={response.duration}s sample_rate={response.sample_rate} file_size={response.file_size}B")

    # 流式：分块产出，末块才带 audio_path
    chunks = 0
    async for chunk in plugin.synthesize_stream(TTSRequest(text="流式合成演示一二三四五六七八九十")):
        chunks += 1
    print(f"stream chunks={chunks}")

    health = await plugin.health()
    print(f"health status={health.status} healthy={health.healthy} loaded_models={health.loaded_models}")

    await plugin.unload(ExampleTonePlugin.DEFAULT_MODEL_ID)
    await plugin.shutdown()
    await plugin.shutdown()  # 幂等：重复调用必须安全
    print("shutdown ok (idempotent)")
    print("SELFTEST PASSED")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_self_check())