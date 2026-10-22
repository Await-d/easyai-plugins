# 排错手册

按**症状**查，不用从头读。

---

## 一、校验相关

### `[FAIL] xxx: manifest error: 不允许的额外属性`

某一段多写了字段。最常见的是 `isolation.venv`——venv 由宿主创建，**不该出现在 manifest 里**。

```diff
  "isolation": {
    "mode": "subprocess",
-   "venv": ".venv"          ← 删掉
  }
```

### `'true' is not of type 'array'`（或 `network` 相关类型错）

`permissions.network` 必须是**域名数组**：

```diff
- "network": true
+ "network": ["huggingface.co"]
```

### `'model_id' was unexpected` / `required ['name']`

模型条目字段名是 **`name`**：

```diff
- { "model_id": "whisper-v1" }
+ { "name": "whisper-v1" }
```

### `'1.2' does not match '^\d+\.\d+\.\d+$'`

`plugin.version` 必须三段：`1.2.0`。

### `api_version` 不匹配

只支持 `>= <= == != > < =` 组合。`~>1.0`、`^1.0` 都不支持。

### `entry_point` 不匹配

- 只能是 `module:Class`（**冒号**，不是 `module.py:Class`）；
- 模块名必须是合法 Python 标识符 —— **文件名不能有 `-`**。
  `acme-whisper.py` 无法被导入，改成 `acme_whisper.py`。

### `目录名必须等于 plugin.id`

```
plugins/acme-whisper-tts/   ← 目录
plugin.json: "id": "acme.whisper-tts"   ← 不一致
```

两处必须**逐字符相同**。宿主按 id 找目录，不一致会导致"装了却 404"。

### `契约方法缺失: ['synthesize_stream']`

8 个方法缺一不可，且 `synthesize_stream` 必须是 `async def` + `yield`
（不是普通 `def` 返回列表）。

### `xxx() 无参构造失败`

宿主按 `entry_point` 直接 `Cls()`，**不会传任何参数**。把必填参数改成可选，
或用 `initialize(config)` 接收宿主下发的配置：

```diff
- def __init__(self, model_path: str):
+ def __init__(self) -> None:
```

### `[FAIL] xxx: 缺少 LICENSE` / `LICENSE 内容过短（N 字节）`

每个插件目录都要有**完整许可证文本**。两个常见误区：

| 写法 | 结果 |
|---|---|
| 目录里没有 `LICENSE` | 缺文件 |
| `LICENSE` 里只写一行 `MIT` | 被判过短（阈值 20 字节），等于没有许可条款正文 |
| 目录里放 `LICENSE`（完整 MIT 文本） | ✅ |

正确做法：复制仓库根 `LICENSE` 全文到插件目录，再把版权行改成你自己的名字与年份。

### `[FAIL] xxx: 内容已改动但 version 仍是 x.y.z`

改了 `plugin.json` 或任意 `*.py`，却没改 `plugin.version`。

为什么必须 bump：索引里 `version` 是给宿主判断"有没有新版"用的，`sha256`
判断"包有没有变"。**版本不变而 sha256 变了**，用户要么收不到更新，
要么回滚时定位到错误的包。

```bash
# 只改 README / LICENSE 不要求 bump，但改了代码就要：
#   plugin.json:  "version": "0.1.0"  ->  "0.1.1"
```

> 本地跑 `validate.py` 不带 `--base-ref` 时不检查此项（避免未提交改动误报）；
> CI 会用 `origin/<base>` 比对。

### `[FAIL] xxx: api_version '>=2.0' 不被当前宿主 SDK 1.0.0 满足`

你的区间超出了当前宿主的契约范围 —— 这样的插件**装上也用不了**，
用户会经历安装 → 下载 → 解压 → 激活，最后才在激活阶段报错。

两种处理：

- 你的插件其实能在 1.x 上跑 → 把区间放宽（如 `>=1.0,<2.0`）；
- 确实需要 2.0 的特性 → 等宿主发布 2.0 后再提交（可先开 issue 讨论）。

> 此项仅在**严格模式**（能 `import sdk`）下检查；降级模式会跳过。

### `[FAIL] schema 与主仓不一致`

`schema/plugin-manifest.schema.json` 是**主仓的副本**，不是本仓的独立定义。

**不要在本仓改规则**——改了会让 CI 与宿主用两套校验，PR 全绿但装不上。
正确做法是用主仓文件覆盖：

```bash
cp /path/to/EasyAI/backend/sdk/schema/plugin-manifest.schema.json schema/
```

> CI 用 `--core-schema` 与主仓比对；拿不到主仓时会 `WARN` 跳过，而不是假装一致。

### `导入模块失败 ... ModuleNotFoundError: No module named 'sdk'`

插件代码 `import sdk`，但当前环境找不到契约包。设置：

```bash
export PYTHONPATH=/path/to/EasyAI/backend
```

### 输出 `模式: schema-only` 而不是 `模式: sdk`

同上。降级模式**不检查**继承关系与无参构造，属于"部分校验"。
CI 里也会出现同样提示，原因通常是没配 `EASYAI_CORE_TOKEN`（主仓为私有仓库）。

### `[FAIL] 无法导入 EasyAI SDK ... 也没有 jsonschema 包`

两种修法任选：

```bash
# 严格模式
export PYTHONPATH=/path/to/EasyAI/backend
# 或降级模式
pip install "jsonschema>=4.0"
```

---

## 二、安装 / 激活

### `install` 返回 **404 插件不存在**

宿主只从**本地插件根**扫描，最常见三种原因：

1. 目录没放进去。默认 `<项目根>/.easyai/plugins`，可用 `EASYAI_PLUGIN_ROOT` 覆盖；
2. **目录名不等于 `plugin.id`**；
3. 目录里缺 `plugin.json`。

### `activate` 返回 **422**

manifest 校验失败或**硬件硬约束不满足**。看 `detail.reasons`——
会明确写"requires backend=cuda，实际 cpu"这类原因。

若你确实想跑 CPU：

```json
"hardware": {
  "requires": { "backend": ["cuda", "cpu"], "min_vram_gb": 0 },
  "cpu_fallback": true
}
```

> `cpu_fallback` 只有在 `requires.backend` 里**显式包含 `cpu`** 时才授予降级许可。

### `activate` 返回 **409**

状态不允许（例如未安装就激活）。先 `install` 再 `activate`。

### 激活成功但行为是旧代码

运行时句柄还在用旧模块。`deactivate` → 改代码 → `activate`；
`in_process` 模式下可能需**重启宿主**（模块已在 `sys.modules` 里）。

---

## 三、合成

### `synthesize` 返回 **409**

插件未激活。注意 `active` 是**运行时真的持有句柄**，不只是注册表状态。

### `synthesize` 返回 **422**

请求参数非法（空文本、`speed` 越界等）。`TTSRequest.validate()` 抛的就是这类。

### `synthesize` 返回 **502**

插件内部失败。`detail.error_code` 是插件侧原始错误码
（如 `MODEL_NOT_LOADED`），`detail.reasons` 有堆栈摘要。

### `synthesize` 返回 **503**

`subprocess` 模式的 worker 崩溃 / 重启耗尽 / 心跳超时。可重试；
反复出现说明插件在 `initialize`/`load` 阶段就崩了，看宿主日志里的 stderr 尾部。

### 合成成功但**没有 `audio_url`**

两个字面原因：

1. 请求里没带 `request_id`，且插件响应里也没有 —— 宿主无法登记，故不返回 URL；
2. 插件返回的 `audio_path` 指向的文件**不满足登记条件**：不是文件、后缀不在白名单
   （`.wav/.mp3/.flac/.ogg/.m4a/.aac/.opus/.webm`）、或大小为 0。

**带上 `request_id` 再试**：

```bash
curl -X POST localhost:8000/api/plugins/<id>/synthesize \
     -H 'Content-Type: application/json' \
     -d '{"text":"hi","request_id":"dev-1"}'
```

### 有 `audio_url` 但播放 404

- 宿主重启过：登记表是**进程内**的，重启即清空；
- `request_id` 写错。

### 有 `audio_url` 但播放 410

登记还在，但文件没了——常见于插件 `shutdown()` 清理了临时目录。
重新合成即可（这正是 404/410 分开的原因：410 不需要查调用方）。

### 流式合成取消不生效

`async def` 里没有让出事件循环。纯同步循环会让 `cancel()` 永远无法投递：

```python
for chunk in chunks:
    yield ...
    await asyncio.sleep(0)   # ⭐ 必须有让出点
```

---

## 四、市场索引

### 市场列表为空

```bash
curl localhost:8000/api/market/index/status
```

看 `degraded` / `degrade_reason`：

| 现象 | 原因 |
|---|---|
| `degrade_reason` 含"未配置 index 地址" | 没设 `EASYAI_MARKET_INDEX_URL` |
| 拉取失败但有缓存 | 走缓存，属正常降级 |
| 拉取失败且无缓存 | 网络 / URL 错 / 非 `https` 或 `file` scheme |

### 条目显示"不可安装"

`installable` 要求条目内嵌 **合法 manifest**。若 index 里只有 `plugin_id`
没有 `manifest`，宿主无法判定，会标不可安装。

本仓的 `index.json` 由 `build_index.py` 自动内嵌 manifest，正常不会出现。

### 条目显示"不可下载"

`download_url` 为空或被作废（scheme 不是 `https`/`file`）。
本仓 CI 生成的地址是 `https://github.com/.../releases/download/latest/...`。

---

## 五、CI

### `Validate Plugins` 失败：`could not read Username for 'https://github.com'`

主仓是私有仓库，CI 无法匿名 clone。配置 secret `EASYAI_CORE_TOKEN`
（细粒度 PAT，仅需对主仓 `Contents: Read`）。

不配也能绿，但会是 `schema-only` 降级模式。

### `Build Index` 报 `fail_on_unmatched_files`

`dist/*.zip` 没产出。通常是某个插件的 `plugin.json` 不合法导致打包前就退出，
看这一步的日志里 `[FAIL]` 开头的行。

### PR 被拒：`index.json 是生成物，请勿手工修改`

删掉对 `index.json` 的改动，只提交 `plugins/` 下的内容。合并后 CI 会重建。
