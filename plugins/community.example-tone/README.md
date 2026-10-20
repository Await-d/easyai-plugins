# Community Example Tone

一个**零第三方依赖**的示例 TTS 引擎：用正弦波叠加合成可听的音调序列，输出
16bit / 22050Hz / 单声道 WAV。

它同时是本仓的**收录样板** —— 目录结构、manifest 字段、契约实现都按收录要求写好，
可以直接复制改造成你自己的插件。

## 能力

| 能力 | 说明 |
|---|---|
| `streaming` | `synthesize_stream` 分块产出，中间块 `RUNNING`、末块 `COMPLETED` |
| `cancel` | 通过事件循环让出点响应取消，未完成的半成品会被清理 |
| `speed_control` / `pitch_control` / `volume_control` | 语音参数生效 |

## 模型

`models.discovery = "explicit"`，默认模型 `template-tone-v1`。
**不需要下载任何权重** —— 音色表在 `load()` 里即时计算，`vram_used_mb = 0`。

## 安装

```bash
# 1) 解压到插件根（默认 <项目根>/.easyai/plugins）
#    目标目录必须以 plugin_id 命名：community.example-tone/
# 2) 安装并激活
curl -X POST http://127.0.0.1:8000/api/plugins/install \
     -H 'Content-Type: application/json' -d '{"plugin_id":"community.example-tone"}'
curl -X POST http://127.0.0.1:8000/api/plugins/community.example-tone/activate
```

> 宿主目前**尚不支持从索引一键安装**，需手动解压。详见仓库根 README。

## 自检

不依赖宿主，可直接运行（需要能 `import sdk`）：

```bash
PYTHONPATH=/path/to/EasyAI/backend python example_tone.py
```

预期输出包含 `SELFTEST PASSED`，并在临时目录生成一个可播放的 WAV。

## 已知限制

- 只合成**音调序列**，不是真正的语音合成；它验证的是链路，不是音质；
- 只支持 `wav` 输出格式；
- `in_process` 隔离：与宿主同进程，异常会直接影响宿主（生产插件建议评估 `subprocess`）。

## 作为你插件的起点

复制本目录，按下面 4 处改：

1. `plugin.json` → `plugin.id`（必须含点）、`name`、`version`、`entry_point`；
2. 适配器文件名 → 起一个**全局唯一**的名字（不要用 `adapter.py`）；
3. 适配器里的类名 → 与 `entry_point` 的冒号后部分一致；
4. `synthesize` 的实现 → 换成你的推理调用。

完整规范见主仓 `docs/plugin-dev-guide.md`，收录要求见本仓 `CONTRIBUTING.md`。
