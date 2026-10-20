# `plugin.json` 字段参考

本文是 `plugins/<id>/plugin.json` 的速查表，**以
[`schema/plugin-manifest.schema.json`](../schema/plugin-manifest.schema.json) 为准**。
schema 与本文不一致时，以 schema 为准（并请提 issue 修正本文）。

---

## 顶层结构

只有 `plugin` 是必填段，其余可省略。**所有段都禁止未知字段**
（`additionalProperties: false`），多写一个键就会报错。

```json
{
  "plugin":       { ... },   // 必填
  "isolation":    { ... },   // 可选
  "hardware":     { ... },   // 可选（但缺省会出 warning）
  "dependencies": { ... },   // 可选
  "permissions":  { ... },   // 可选
  "models":       { ... },   // 可选
  "contributes":  { ... }    // 可选
}
```

---

## 1. `plugin`（必填）

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `id` | string | ✅ | 长度 ≥ 3，**必须含点**，`^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$` |
| `name` | string | ✅ | 非空 |
| `version` | string | ✅ | **严格三段** `^\d+\.\d+\.\d+$` |
| `engine_kind` | string | ✅ | `tts` / `asr` / `llm` / `audio_processing` / `embedding` |
| `api_version` | string | ✅ | 逗号分隔的版本子句，如 `>=1.0,<2.0` |
| `entry_point` | string | ✅ | `module:Class`，模块须为合法 Python 标识符 |
| `license` | string | | 建议填 `MIT` / `Apache-2.0` 等 SPDX 标识 |
| `authors` | string[] | | 建议写 `姓名 <邮箱>` |
| `description` | string | | 面向用户，说明做什么、依赖什么 |
| `homepage` | string | | 项目主页 |

**`api_version` 支持的运算符**：`>=` `<=` `==` `!=` `>` `<` `=`。
**不支持** `~>`、`^`、`*` 等 —— 宿主会直接拒绝（装不上，而不是降级）。

**`entry_point` 的两条硬约束**：

- 只能是冒号形式，`module.py:Class` 是错的；
- 模块名必须是合法 Python 标识符 —— **不能含 `-`**。
  所以 `acme-whisper.py` 这种文件名无法被导入，要写成 `acme_whisper.py`。

---

## 2. `isolation`

| 字段 | 类型 | 约束 |
|---|---|---|
| `mode` | string | `in_process` / `subprocess` / `service` |
| `python` | string \| null | 版本区间，如 `>=3.10,<3.12` |

- `service` **当前宿主明确不支持**，声明了也激活不了，请勿使用；
- **没有** `isolation.venv` 字段 —— venv 由宿主按 `mode` 自动创建，
  写上去会被判为"多余属性"。

---

## 3. `hardware`

```json
{
  "hardware": {
    "requires":   { "backend": ["cuda"], "min_vram_gb": 8 },
    "recommends": { "backend": ["cuda"], "min_vram_gb": 12 },
    "cpu_fallback": false
  }
}
```

| 字段 | 说明 |
|---|---|
| `requires` | **硬约束**。不满足时宿主拒绝安装/激活（422） |
| `recommends` | **软推荐**。不满足只降分，不阻止 |
| `cpu_fallback` | 是否允许退化到 CPU。**只有 `requires.backend` 里显式包含 `cpu` 才会被授予降级许可** |

`backend` 取值：`cuda` / `rocm` / `directml` / `zluda` / `cpu`。

> 段存在但内容为空也可以；**整段缺失会产生 warning**。想出零 warning 就写上。

---

## 4. `dependencies`

| 字段 | 类型 | 说明 |
|---|---|---|
| `python_packages` | string[] | 如 `["numpy==1.26.4"]`；宿主会建独立 venv 安装 |
| `pip_index_url` | string \| null | **只允许 `https` / `file`**，明文 `http` 被拒 |
| `blacklist_conflicts` | string[] | 已知冲突包 |
| `post_install` | string[] | 安装后脚本；会被逐条审查，尽量不用 |

被**直接拒绝**的包名：`pip`、`pip-tools`、`easy_install`、`distribute`
（防止插件篡改供给链）。

---

## 5. `permissions`

| 字段 | 类型 | 说明 |
|---|---|---|
| `filesystem` | string[] | 逻辑目录，如 `["models", "cache"]`（不是绝对路径） |
| `network` | string[] | **域名数组**，如 `["huggingface.co"]` —— **写 `true` 会报类型错** |
| `gpu` | boolean | 是否使用 GPU |

> `permissions.network` 写 boolean 是本仓最容易犯的错，schema 里它是数组。

---

## 6. `models`

| 字段 | 类型 | 说明 |
|---|---|---|
| `discovery` | string | `explicit`（默认）/ `huggingface` / `modelscope` |
| `default` | string | 默认模型名 |
| `explicit` | object[] | `discovery=explicit` 时的模型清单 |

`explicit[]` 每项：

| 字段 | 必填 | 说明 |
|---|---|---|
| **`name`** | ✅ | **字段名是 `name`，不是 `model_id`** |
| `url` | | 下载地址 |
| `sha256` | | 64 位十六进制 |

> `discovery=explicit` 时，`explicit` 或 `default` 至少一个非空，否则会产生 warning。

---

## 7. `contributes`

| 字段 | 类型 | 说明 |
|---|---|---|
| `capabilities` | string[] | 能力标识，**必须是已知值**（见下） |
| `params_schema` | object | 参数的 JSON Schema，供前端渲染 |
| `ui` | object | `icon` / `category` / `tags` / `description` |

已知能力标识（`sdk.capabilities.CAPABILITY_FLAGS`）：

```
streaming · batch · cancel · voice_cloning · voice_conversion
emotion_control · speed_control · pitch_control · volume_control
multiple_speakers · ssml · custom_models
```

写未知标识会产生 warning（严格模式下也可能被拒）。

---

## 常见违规 → 报错含义

| 报错片段 | 原因 | 修法 |
|---|---|---|
| `不允许的额外属性` / `Additional properties are not allowed` | 某段多写了字段 | 删掉；特别注意 `isolation.venv` |
| `'true' is not of type 'array'`（network） | `permissions.network` 写了布尔 | 改成域名数组 |
| `'model_id' ... required ['name']` | 模型条目用了 `model_id` | 改名为 `name` |
| `'1.2' does not match '^\d+\.\d+\.\d+$'` | 版本只有两段 | 写 `1.2.0` |
| `does not match ... api_version` | 用了 `~>` 等不支持的运算符 | 用 `>=`/`<` 组合 |
| `does not match ... entry_point` | 用了 `module.py:Class` 或模块名含 `-` | 改 `module:Class`，文件名用下划线 |
| `'service' is not one of [...]` | 隔离级别拼错 | 用 `in_process` / `subprocess` |
| `缺少 plugin 段` | 顶层结构不对 | 必须有 `{"plugin": {...}}` |

---

## 零 error + 零 warning 的最小条件

1. `plugin` 六个必填字段齐全；
2. **`hardware` 段存在**（内容可空）；
3. `contributes.capabilities` 的每一项都在已知标识里；
4. `models.discovery == "explicit"` 时，`explicit` 或 `default` 非空；
5. 没有未知字段。

示例插件 `plugins/community.example-tone` 满足以上全部条件，可直接对照。
