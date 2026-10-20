# 从零创建一个插件

本文从**空目录**开始，带你做出一个能被宿主安装、激活并真正合成音频的插件。
全程约 5～10 分钟，命令都可直接复制。

> 目标不是做一个"能跑就行"的空壳，而是**理解每个文件为什么存在** ——
> 这样换成你自己的推理引擎时，不会改一处坏一处。

---

## 0. 前置条件

| 需要 | 说明 |
|---|---|
| Python ≥ 3.10 | 与 `plugin.json` 的 `isolation.python` 一致 |
| EasyAI 主仓 | 提供 `sdk` 契约包（`backend/sdk/`） |
| 能跑 `git` | 提交 PR 用 |

先确认 SDK 可用（这是**严格校验模式**的前提）：

```bash
# Linux / macOS
export PYTHONPATH=/path/to/EasyAI/backend
python -c "import sdk; print('sdk ok', sdk.__file__)"

# Windows PowerShell
$env:PYTHONPATH="E:\01.Projects\EasyAI\backend"
python -c "import sdk; print('sdk ok', sdk.__file__)"
```

预期：打印 `sdk ok .../backend/sdk/__init__.py`。

如果这里就失败，先解决它 —— 后面所有校验都依赖这一步。

---

## 1. 复制示例

仓库自带一个**完整可运行**的示例插件，直接复制它开始改：

```bash
cp -r plugins/community.example-tone plugins/<你的id>
```

Windows：

```powershell
Copy-Item -Recurse plugins/community.example-tone plugins/<你的id>
```

> 目录名将成为 `plugin.id`，**两者必须完全相同**。命名规则：
> 反向域名风格、**必须含点**、只允许 `[A-Za-z0-9._-]`，如 `acme.whisper-tts`。

顺手清掉运行时产物：

```bash
rm -rf plugins/<你的id>/__pycache__
```

---

## 2. 只改 4 处

### 2.1 `plugin.json` 的身份字段

```json
{
  "plugin": {
    "id": "acme.whisper-tts",
    "name": "Acme Whisper TTS",
    "version": "0.1.0",
    "engine_kind": "tts",
    "api_version": ">=1.0,<2.0",
    "entry_point": "acme_whisper_tts:AcmeWhisperTtsPlugin",
    "license": "MIT",
    "authors": ["你的名字 <you@example.com>"],
    "description": "一句话说清它做什么、依赖什么。"
  }
}
```

字段约束见 [manifest-reference.md](./manifest-reference.md)。四个必改项：

| 字段 | 注意 |
|---|---|
| `id` | **必须含点**，且与目录名一致 |
| `version` | **严格三段** `0.1.0`，`0.1` 会被拒 |
| `entry_point` | `模块:类`，模块名须是合法 Python 标识符（不能有 `-`） |
| `name` / `description` | 面向用户，别写"TODO" |

### 2.2 适配器文件名

```bash
mv plugins/<你的id>/example_tone.py plugins/<你的id>/acme_whisper_tts.py
```

**名字必须全局唯一**。`adapter.py` 这种通用名会让多个插件在 `sys.modules` 里互相覆盖，
症状是"激活 A 却跑的是 B 的代码"。

### 2.3 适配器里的类名

```python
class AcmeWhisperTtsPlugin(IEnginePlugin):
    ...
```

类名必须与 `entry_point` 冒号后的部分**完全一致**。

### 2.4 `synthesize` 的实现

把示例里的"正弦波发音"换成你的推理调用。**最关键的一条**：

```python
async def synthesize(self, request: TTSRequest) -> TTSResponse:
    request.validate()          # 先校验，非法参数应抛 ConfigurationError

    out_path = <你的临时文件路径>.with_suffix(".wav")
    # ... 跑推理，把 PCM 写成 16bit / 单声道 WAV ...

    return TTSResponse.completed(
        request.request_id,
        audio_path=str(out_path),   # ⭐ 音频走文件路径，不要塞 base64
        duration=seconds,
        sample_rate=sample_rate,
    )
```

> **为什么必须回 `audio_path` 而不是 base64**：几十秒的 WAV 转 base64 会让 JSON
> 膨胀数倍，且宿主需要落盘才能给浏览器播放。契约里 `TTSResponse.audio_path` 就是为此设计。

---

## 3. 校验

```bash
python scripts/validate.py
```

**预期输出**（严格模式；数量是你当前 `plugins/` 下的插件总数，
照本教程复制后会看到 2 个 —— 示例插件 + 你的新插件）：

```
[OK] 2 个插件全部通过校验（模式: sdk）
     - acme.whisper-tts
     - community.example-tone
```

如果输出是 `模式: schema-only`，说明没拿到 SDK，此时**契约继承与无参构造没被检查**，
请回到第 0 步修好 `PYTHONPATH` 再跑一次。

校验器会检查：

- manifest 通过 schema；
- 目录名 == `plugin.id`；
- `entry_point` 的模块文件存在、类可导入、继承 `IEnginePlugin`；
- **8 个方法齐备**；
- **类可无参构造**；
- 有 `README.md`；
- 插件目录内没有顶层 `__init__.py`。

---

## 4. 不装宿主也能自检

示例插件的文件末尾有自检入口，改完先跑它：

```bash
cd plugins/<你的id>
PYTHONPATH=/path/to/EasyAI/backend python acme_whisper_tts.py
```

**预期输出**（真实跑出来的样子）：

```
synthesize success=True
audio_path=/tmp/easyai-acme.whisper-tts-xxxx/acme-whisper-xxxx.wav
duration=2.28s sample_rate=22050 file_size=100592B
stream chunks=4
health status=ready healthy=True loaded_models=['whisper-v1']
shutdown ok (idempotent)
SELFTEST PASSED
```

这一步能抓出 80% 的实现错误，且不需要启动宿主。

---

## 5. 装进宿主跑通全链路

### 5.1 放到插件根

插件根默认是 `<项目根>/.easyai/plugins`（可用 `EASYAI_PLUGIN_ROOT` 覆盖）。
**目标目录必须以 `plugin.id` 命名**：

```
<插件根>/acme.whisper-tts/plugin.json
<插件根>/acme.whisper-tts/acme_whisper_tts.py
```

### 5.2 安装 → 激活 → 合成

```bash
# 安装（走本地目录扫描）
curl -X POST http://127.0.0.1:8000/api/plugins/install \
     -H 'Content-Type: application/json' \
     -d '{"plugin_id":"acme.whisper-tts"}'

# 激活（返回 runtime 快照，应看到 state=active）
curl -X POST http://127.0.0.1:8000/api/plugins/acme.whisper-tts/activate

# 合成
curl -X POST http://127.0.0.1:8000/api/plugins/acme.whisper-tts/synthesize \
     -H 'Content-Type: application/json' \
     -d '{"text":"你好 EasyAI","request_id":"demo-1"}'
```

响应里会有：

```json
{
  "is_success": true,
  "audio_path": "/tmp/.../xxx.wav",
  "audio_url": "/api/plugins/acme.whisper-tts/audio/demo-1",
  "audio_size": 100592
}
```

`audio_url` 是**宿主登记的受控出口**，可直接给 `<audio src>` 播放；
`audio_path` 只用于排障，不要给前端拼路径（插件音频在系统临时目录，浏览器读不到）。

### 5.3 检查健康状态

```bash
curl http://127.0.0.1:8000/api/plugins/acme.whisper-tts/health
```

---

## 6. 8 个契约方法

| 方法 | 何时调用 | 关键要求 |
|---|---|---|
| `initialize(config)` | 激活时最先调用 | 声明能力；不要在这里加载权重 |
| `load(model_spec)` | `initialize` 之后 | **慢操作放这里**（加载权重、预热）；失败抛 `EngineLoadError` |
| `unload(model_id)` | 释放单个模型 | **必须幂等**，不存在时静默成功 |
| `synthesize(request)` | 非流式合成 | 先 `request.validate()`；返回 `audio_path` |
| `synthesize_stream(request)` | 流式合成 | **`async def` + `yield`**；中间块 `RUNNING`，末块 `COMPLETED` |
| `cancel(request_id)` | 取消 | 返回是否受理；不支持时返回 `False` |
| `health()` | 健康检查 | 返回 `HealthStatus`；**`EngineState` 里没有 `healthy`**，健康看 `HealthStatus.healthy` |
| `shutdown()` | 停用 / 宿主退出 | **必须幂等**；清理临时文件 |

状态机（简化）：

```
created → initializing → initialized → loading → ready
                                       ↘ busy（合成中）
ready/busy → unloading → initialized
任意状态 → error → shutdown
```

---

## 7. 流式与取消怎么写

```python
async def synthesize_stream(self, request: TTSRequest) -> AsyncIterator[TTSResponse]:
    request.validate()
    out_path = self._new_wav_path()

    for index, chunk in enumerate(self._render_chunks(request)):
        if request.request_id in self._cancelled:
            return                      # 取消：直接结束生成器
        yield TTSResponse(
            request_id=request.request_id,
            status=TaskStatus.RUNNING,  # 末块才是 COMPLETED
            audio_path=None,            # 中间块不带路径
        )
        await asyncio.sleep(0)          # ⭐ 让出事件循环，cancel() 才能投递进来

    yield TTSResponse(
        request_id=request.request_id,
        status=TaskStatus.COMPLETED,
        audio_path=str(out_path),       # 末块带完整路径
    )
```

两个要点：

1. **必须有 `await asyncio.sleep(0)`**（或等价让出点）。纯同步循环会让事件循环
   无法处理 `cancel()`，取消永远不生效。
2. **末块才带 `audio_path`**，中间块给 `None`。

---

## 8. 声明依赖

如果插件需要第三方包，写进 `dependencies`：

```json
{
  "dependencies": {
    "python_packages": ["numpy==1.26.4", "soundfile>=0.12"],
    "pip_index_url": null,
    "blacklist_conflicts": [],
    "post_install": []
  }
}
```

宿主会为它建**独立 venv** 并安装（不会污染宿主环境）。注意：

- `pip` / `pip-tools` / `easy_install` 等包名会被**拒绝**；
- `pip_index_url` 只允许 `https` / `file`，明文 `http` 会被拒；
- `post_install` 会被逐条审查，能不用就不用。

---

## 9. 隔离级别怎么选

| `isolation.mode` | 含义 | 适用 |
|---|---|---|
| `in_process` | 与宿主同进程 | 零依赖、轻量、可信；异常会直接影响宿主 |
| `subprocess` | 每插件独立进程 + 独立 venv | **推荐默认**；崩溃不拖垮宿主，依赖隔离干净 |
| `service` | 独立服务 | **当前宿主明确不支持，不要声明** |

示例插件用 `in_process` 是为了让自检能脱离宿主直接跑；
生产插件建议 `subprocess`。

---

## 10. 提交

```bash
python scripts/validate.py          # 必须全绿
git checkout -b plugin/acme-whisper-tts
git add plugins/acme.whisper-tts
git commit -m "feat(plugin): add acme.whisper-tts"
git push -u origin plugin/acme-whisper-tts
gh pr create --fill
```

**不要改 `index.json`** —— CI 会检测并直接拒绝。它由合并后的工作流自动重建。

审核红线（会被直接拒绝）见 [CONTRIBUTING.md](../CONTRIBUTING.md) 第 4 节。
