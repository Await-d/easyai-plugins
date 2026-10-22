# EasyAI Community Plugins 项目规则

**Generated:** 2026-10-22 | **Branch:** main | **Repo:** `Await-d/easyai-plugins`

本文件是本仓的根级执行规范，适用于所有 AI Agent 与人类贡献者。

本仓是 **独立 git 仓库**：主仓 EasyAI 负责宿主、SDK 契约与内置插件；本仓负责社区插件的
收录、校验、打包与索引分发。两条仓库边界不得混淆：

- 在本仓工作时，`git` 命令只操作本仓，禁止从主仓 `git add` 本目录；
- 契约语义（8 个方法、DTO、状态机）以主仓 `backend/sdk/` 与
  `docs/plugin-dev-guide.md` 为准，本仓的收录与安全规则以 `CONTRIBUTING.md` 和本文件为准；
- 文档、schema 与代码冲突时，**以代码与 schema 为准**。

## 概述

EasyAI 社区引擎插件仓库：插件作者提交插件目录，本仓负责校验 manifest 与契约、
生成可复现的插件包、维护市场索引 `index.json`。

技术栈与运行前提：

- Python 3.10+（CI 使用 3.11）
- 插件适配器优先零第三方依赖；需要依赖时写入 `plugin.json` 的
  `dependencies.python_packages`，由宿主创建独立 venv 安装
- 校验脚本只依赖标准库与可选的 `jsonschema>=4.0`
- 宿主 SDK 不在本仓 vendored，通过 `PYTHONPATH` 指向主仓 `backend/`
- CI：`.github/workflows/validate.yml`（PR 校验）+
  `.github/workflows/build-index.yml`（合并后打包与重建索引）

## 项目结构

```text
easyai-plugins/
├── plugins/
│   └── <publisher>.<plugin-id>/     # 目录名必须等于 plugin.json 的 plugin.id
│       ├── plugin.json              # manifest，单一真相源
│       ├── <unique_module>.py       # 适配器，模块名必须全局唯一
│       ├── README.md                # 必需：用途、依赖、模型获取方式、已知限制
│       └── test_*.py                # 可选，鼓励；stdlib unittest
├── schema/
│   └── plugin-manifest.schema.json  # 从主仓同步的副本，禁止单独修改
├── scripts/
│   ├── validate.py                  # 收录校验（CI 与本地共用）
│   └── build_index.py               # 生成 index.json / 打包（唯一产出方式）
├── docs/                            # 面向插件作者的教程、字段参考、排障
├── index.json                       # 生成物，禁止手改（CI 重建）
├── .github/workflows/
│   ├── validate.yml                 # PR 校验 + 产物/索引检查
│   └── build-index.yml              # 合并 main 后打包 + 发布 Release + 重建索引
├── .gitattributes                   # 统一 LF，保证跨平台可复现
├── CONTRIBUTING.md                  # 收录规则与审核红线
└── README.md                        # 仓库定位、宿主接入、索引机制
```

## 快速定位

| 任务 | 位置 | 备注 |
|------|------|------|
| 新增插件 | `plugins/<publisher>.<plugin-id>/` | 目录名必须等于 `plugin.id`，id 必须含点 |
| 修改 manifest | `plugins/<id>/plugin.json` | 以 schema 为准；禁止多余字段 |
| 实现适配器 | `plugins/<id>/<unique_module>.py` | 继承 `IEnginePlugin`，实现 8 个方法 |
| 写插件说明 | `plugins/<id>/README.md` | 必需；不得声称未验证的能力 |
| 加插件测试 | `plugins/<id>/test_<id>.py` | stdlib unittest；生命周期冒烟 |
| 校验收录 | `scripts/validate.py` | PR 前必须全绿；严格模式需 SDK |
| 生成索引 | `scripts/build_index.py` | 唯一产出方式，禁止手改 `index.json` |
| 修改 schema | `schema/plugin-manifest.schema.json` | 只能从主仓同步，不单独改 |
| 查看 manifest 规则 | `docs/manifest-reference.md` | 字段、约束、报错含义 |
| 本地调试 / 发布 | `docs/local-development.md` | 含宿主内安装、激活、合成 |
| 排障 | `docs/troubleshooting.md` | 按症状查 |
| 审核红线 | `CONTRIBUTING.md` 第 4 节 | 安全类直接拒绝 |
| 契约定义 | 主仓 `backend/sdk/contracts.py` | 8 个方法、DTO、异常 |
| 完整插件开发指南 | 主仓 `docs/plugin-dev-guide.md` | 契约语义与隔离级别 |

## 代码规范

### 插件适配器（Python）

- 4 空格缩进；模块/函数/变量 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`。
- 文件头使用 `from __future__ import annotations`；公共方法写类型注解。
- 插件类必须继承 `sdk.IEnginePlugin`，实现 8 个方法：
  `initialize` / `load` / `unload` / `synthesize` / `synthesize_stream` / `cancel` /
  `health` / `shutdown`。
- **`__init__` 必须能无参构造**：宿主按 `entry_point` 直接 `Cls()`，任何必填构造参数
  都会让 `activate` 失败。重依赖放到 `load()`，不要放在构造期。
- **模块名必须全局唯一**：禁止 `adapter.py` / `plugin.py` / `engine.py` / `main.py`；
  推荐 `<plugin_name>_adapter.py` 或 `<plugin_name>_plugin.py`。
  模块名必须是合法 Python 标识符，不能含 `-`。
- `entry_point` 必须是 `module:Class`；模块名等于文件名去掉 `.py`；类名与冒号后完全一致。
- **音频只走文件路径**：`TTSResponse.audio_path` 指向落盘 WAV，禁止 base64 塞进 JSON。
- WAV 统一 16bit 小端 PCM；单声道/采样率在 README 中说明。
- `synthesize_stream` 必须是 `async def` + `yield`；中间块 `RUNNING`、末块 `COMPLETED`，
  中间块 `audio_path=None`；循环内必须有 `await asyncio.sleep(0)` 或等价让出点，
  否则 `cancel()` 永远投递不进来。
- `unload` / `shutdown` **必须幂等**；重复调用不得抛异常，并清理临时文件。
- `health()` 返回 `HealthStatus`；注意 `EngineState` 没有 `healthy` 取值，
  健康态用 `status=READY` + `healthy=True`。
- 异常使用 SDK 异常体系（如 `ConfigurationError` / `EngineLoadError`）；
  禁止空 `except`、禁止 `except: pass`、禁止吞掉异常后返回成功。
- 新增/修改的关键逻辑必须附中文注释，说明"为什么"与兼容约束。
- 优先标准库；引入第三方依赖必须写入 manifest 并说明用途，不得依赖宿主环境里"碰巧存在"的包。
- 插件目录内**禁止**顶层 `__init__.py`：它会把目录变成包，与宿主"加 `sys.path` 后按模块名导入"
  的约定冲突。

### manifest（`plugin.json`）

- 以 `plugins/community.example-tone/plugin.json` 为起点；字段规范见
  `docs/manifest-reference.md`。
- 必填：`plugin.id` / `name` / `version` / `engine_kind` / `api_version` / `entry_point`。
- `plugin.id` 必须含至少一个点，只允许 `[A-Za-z0-9._-]`，且**必须等于目录名**。
- `version` 必须是严格三段 `x.y.z`；修改已有插件**必须升版本**。
- `api_version` 只支持 `>= <= == != > < =` 组合，不支持 `~>`。
- `entry_point` 只能 `module:Class` 形式；module 不能含 `-`。
- `permissions.network` 必须是**域名数组**（如 `["api.example.com"]`），写 `true` 会失败；
  没有网络访问就写 `[]`。
- `models.explicit[].name` 是字段名，不要写 `model_id`；无模型也要用
  `discovery: "explicit"` + 一个占位条目，不能写 `"none"`。
- 禁止 schema 未定义的额外属性（例如 `isolation.venv` 是多余的，venv 由宿主管理）。
- `isolation.mode` 只使用 `in_process` / `subprocess`；`service` 当前宿主不支持，不要声明。
  生产插件优先 `subprocess`。
- manifest 校验必须 **0 error + 0 warning**；warning 也视为待修复项。
- 不得声明未实现的能力（`contributes.capabilities` 必须与代码一致）。

### 脚本与索引

- `scripts/validate.py`：只做校验，不修改任何文件；退出码 0/1。
- `scripts/build_index.py`：索引的**唯一**产出方式；禁止手写或手改 `index.json`。
- 打包必须可复现：文件排序、固定 zip 时间戳 `1980-01-01`、固定压缩级别、
  文本文件 CRLF → LF；排除 `__pycache__` / `.venv` / `*.pyc` / `.git`。
- 脚本只依赖标准库；`jsonschema` 仅用于降级校验，缺失时必须打印明确 WARN，
  不得静默伪装成严格模式。
- 禁止在脚本中写死本机绝对路径；仓库地址由 `--repo` 或 `GITHUB_REPOSITORY` 提供。
- 修改打包/索引逻辑时必须同步更新 `README.md`、`docs/` 与 CI 注释；
  可复现性一旦破坏，`sha256` 会持续漂移。
- `schema/plugin-manifest.schema.json` 是从主仓同步的副本；升级 schema 时整文件覆盖，
  不做局部手改，并确认 `validate.py` 的降级模式仍可用。

### 文件规模约束

**硬性红线（新增文件必须满足，存量文件不得继续增长）：**

| 范围 | 红线 | 说明 |
|------|------|------|
| Python 单文件（插件 / 脚本） | **1000 行** | 空行、纯注释行不计入 |
| Markdown 单文件（文档） | **600 行** | 避免单篇教程膨胀到无法维护 |

**目标值（新增文件应尽量达成，不阻断 CI）：**

| 文件类型 | 目标值 |
|---------|--------|
| 插件适配器 | 400 行，超出需抽 helper / 拆模块 |
| 校验与构建脚本 | 500 行 |
| `plugin.json` | 200 行 |
| 插件 `README.md` | 150 行 |

**豁免清单（不适用上述红线）：**

- 测试文件：`test_*.py`、`*_test.py`
- 生成物：`index.json`、`dist/`、`__pycache__/`、`*.pyc`
- 同步副本：`schema/plugin-manifest.schema.json`（主仓 schema 的镜像）
- 纯声明类：枚举、常量表、类型定义

**存量超线文件的处理（ratchet 机制）：**

- 当前最大的 `plugins/community.example-tone/example_tone.py` 为 901 行（总行数口径），
  在 1000 行红线内；本仓目前**没有存量超线文件**。
- 新增文件、或对已达标文件的改动，必须满足红线；出现超线时登记基线，只降不升。
- 主动拆分后基线自动下调，不得回退。
- 当前没有自动行数门禁；由代码评审执行。接入时保持本表口径，
  **新增文件严禁加入基线**。
- **严禁**通过 `# noqa`、注释掉规则等方式绕过。

**方法级配套约束（同等强制）：**

- 单函数 ≤ 40 行；依赖参数 ≤ 7 个；单类 public 方法 ≤ 20 个；圈复杂度 ≤ 10
- 单文件 `import` 超过 25 条视为职责过杂，需拆分（测试文件除外）

**执行方式：**

- 本仓的强制门禁是 `python scripts/validate.py`（CI `validate.yml` 与本地共用）：
  manifest schema、目录名/id 一致性、`entry_point` 可解析、8 个方法、无参构造、
  `README.md` 与 **`LICENSE`（完整文本，非单行 SPDX）**、无顶层 `__init__.py`、
  **`plugin.api_version` 能被当前宿主 SDK 满足**（仅严格模式）与
  **`deprecations.json` 的一致性**（key 与 `replacement` 必须都是已收录的 id、
  不得自我替代）。
- CI 还检查：**改了 `plugin.json` / `*.py` 必须 bump `version`**（`--base-ref`
  与基准分支比对，只改文档不要求）；**`schema/` 副本必须与主仓逐字一致**
  （`--core-schema`）；禁止提交 `__pycache__` / `*.pyc` / `.venv`；
  PR 中禁止手改 `index.json`。
- CI 目前**不运行**插件单元测试；`test_*.py` 与插件自检需在本地执行。
- 行数门禁尚未接入；接入时再补 `max-lines` 等价规则或行数脚本。

**拆分产物目录约定：**

```text
plugins/<publisher>.<plugin-id>/
├── plugin.json
├── <plugin_name>_adapter.py     # 入口模块，模块名全局唯一
├── <plugin_name>_utils.py       # 拆分出的纯函数 / 数据结构
├── test_<plugin_name>.py        # 契约与生命周期测试
└── README.md
```

- 拆分件必须留在插件目录内，**不得**把公共工具放到仓库根或跨插件共享
  —— 每个插件包必须是自包含的，宿主只加插件目录到 `sys.path`。
- 拆分后的模块名仍需全局唯一；禁止 `utils.py` / `helpers.py` 这类通用名。
- 插件目录内不得出现顶层 `__init__.py`；需要子包时在 manifest 中声明 `a.b:Class`，
  并确认 `validate.py` 的模块解析能命中。

## 依赖与锁文件

- 本仓**没有** `requirements.txt` / lockfile；校验脚本只依赖标准库 + 可选 `jsonschema>=4.0`。
- 插件运行时依赖写入 `plugin.json` 的 `dependencies.python_packages`，
  由宿主创建独立 venv 安装；**不要**在插件目录内提交 `venv/` 或 wheel。
- 依赖必须来自可核实的 PyPI 包名，尽量锁定版本；`pip` / `pip-tools` / `easy_install`
  等包名会被宿主拒绝。
- `pip_index_url` 只允许 `https` / `file`；明文 `http` 会被拒。
- `post_install` 会被逐条审查，能不用就不用。
- 模型权重不进仓库；用 `models.explicit[].url` 指向官方发布地址，必要时提供 `sha256`。
- SDK 不 vendored：本地通过 `PYTHONPATH=<EasyAI>/backend` 使用；CI 通过
  `EASYAI_CORE_TOKEN` 克隆主仓。禁止把 `sdk/` 复制进本仓当依赖。
- Python 版本：插件 `isolation.python` ≥ 3.10；CI 使用 3.11。
- 升级 schema 或校验规则时，必须同时说明对已收录插件的兼容性影响。

## 禁止事项

| 类别 | 禁止 |
|------|------|
| 索引 | 手工修改 `index.json`；在 PR 中提交 `index.json` 变更 |
| schema | 单独修改 `schema/plugin-manifest.schema.json` 而不从主仓同步 |
| 目录 | 插件目录名与 `plugin.id` 不一致；插件目录内出现顶层 `__init__.py` |
| 模块 | 使用 `adapter.py` / `plugin.py` / `engine.py` / `main.py` 等通用模块名 |
| 构造 | `__init__` 存在必填参数；构造期加载重依赖 |
| 网络 | 向 `permissions.network` 未声明的地址发起请求 |
| 文件 | 读取插件目录外的文件（用户主目录、系统目录、其他插件目录） |
| 执行 | `subprocess` 起任意进程；`eval` / `exec` 远程内容 |
| 产物 | 提交 `.exe` / `.dll` / `.so` / `.pyd` / 模型权重 / `__pycache__` / `*.pyc` / `.venv` |
| 行为 | 挖矿、代理、凭据收集、与插件功能无关的后台活动；声明与实现不一致 |
| 质量 | 空 `except`、`except: pass`、吞异常；虚假的 `SELFTEST PASSED` |
| 文档 | 未经授权创建 Markdown 文档；声称未验证的能力 |
| 提交 | AI 署名、`Generated with` 标记、装饰性表情符号 |
| 协作 | 回滚、覆盖或顺手重构他人 / 其他 agent 的改动 |

## 工作树与验证边界

- 本仓与主仓是两个独立工作树：在本仓执行 `git` 命令时，确认当前目录是
  `easyai-plugins/`，不要把本仓文件提交到主仓，也不要把主仓文件带进本仓。
- 非本轮修改、非自己引入的问题不要主动修复；即使阻断校验，也应先报告为 blocker，
  并说明阻断插件、错误摘要与影响范围。
- 只有在用户明确授权，或该问题确认为本轮改动直接引入时，才允许修复对应错误。
- 共享工作树中遇到其他人或其他 agent 的改动，禁止回滚、覆盖或顺手重构；
  无关变更直接忽略，相关变更按现状兼容。
- 不要为了让 `validate.py` 变绿而删除检查项、放宽 schema 或伪造 `SELFTEST PASSED`。

## 审核红线（安全）

以下情况**直接拒绝**，不接受"只是测试"或"临时用"的解释：

1. **联网回连**：除 `permissions.network` 声明的域名外，不得向任何地址发起请求；
2. **越界读取**：不得读取插件目录外的文件；
3. **执行外部命令 / 动态执行**：不得 `subprocess` 起任意进程，不得 `eval` / `exec` 远程内容；
4. **打包二进制**：不得提交 `.exe` / `.dll` / `.so` / `.pyd` 等预编译产物；
5. **挖矿 / 代理 / 凭据收集**：与插件功能无关的后台活动一律拒绝；
6. **隐藏行为**：manifest 声明的能力必须与实现一致；声明无网络却发请求视为恶意。

补充要求：

- `post_install` 脚本逐条审查；
- 依赖尽量少，必须是 PyPI 上可核实的包名；
- 模型文件不进仓库；
- 不确定是否合规时，先在 PR 描述里说明，不要先斩后奏。

## 发布与索引规则

- `index.json` 由 `scripts/build_index.py` 从 `plugins/*/plugin.json` 聚合生成，
  **永远不要手改**；PR 中修改 `index.json` 会被 CI 直接拒绝。
- 索引条目包含 `plugin_id` / `name` / `version` / **`api_version`** /
  `engine_kind` / `description` / `isolation_mode` / `capabilities` / `license` /
  `homepage` / `publisher` / `tags` / **`versions`（历史版本清单）** /
  **`deprecated` + `replacement`（下线提示，可选）** / 完整 `manifest`。
- `versions` 是**向后兼容的扩展**：宿主当前只读顶层 `version` 与 `sha256`，
  忽略多出来的字段；它存在是为了让市场能提示"还有 1.0.0 可回退"，
  并为将来的版本选择器提供数据。
- 插件目录打成可复现 zip：文件排序、固定时间戳、固定压缩级别、文本 CRLF → LF；
  内容不变时 `sha256` 必须不变。
- `download_url` 指向 `latest` Release；`sha256` 与包内容必须一致。
- 合并到 `main` 后由 `build-index.yml` 自动打包、上传 Release、提交 `index.json`；
  贡献者不需要也不允许手动重建并提交索引。
- 发布前必须升 `plugin.version`（严格三段）；不兼容变更（改 `entry_point`、
  删能力、改模型 id）在 PR 描述中写明，并考虑升主版本。
- 签名验证与权限沙箱属于主仓后续能力（T-21，条件触发）；在此之前，本仓按
  **半可信来源**对待，不要宣称"已签名"或"已沙箱隔离"。

## 远程环境执行规则

当前工作区为 `E:\01.Projects\EasyAI\easyai-plugins`（Windows 本地磁盘）。
若工作区变为远程服务器映射到本地的网络盘符（如 `V:`），在该盘符上执行包管理或构建命令
可能导致文件丢失。

**硬规则：仅当满足以下任一条件时，才允许执行包安装/脚本执行/构建/校验命令：**

1. 操作系统为 Ubuntu（原生 Linux）；
2. 工作区位于本地物理磁盘（非网络映射盘符）且为 Windows。

无法确认是否为网络盘符时，按**禁止**处理。

| 命令类型 | 示例 | Ubuntu 原生 | Windows 本地磁盘 | 网络映射盘符（任意 OS） |
|---------|------|-------------|------------------|--------------------------|
| 包安装/更新 | `pip install jsonschema` | 允许 | 允许 | **禁止** |
| 脚本执行 | `python scripts/validate.py`、`python scripts/build_index.py` | 允许 | 允许 | **禁止** |
| 测试/自检 | `python -m pytest ...`、`python <adapter>.py` | 允许 | 允许 | **禁止** |
| Git 只读 | `git status`、`git log`、`git diff` | 允许 | 允许 | 允许 |
| Git 写入 | `git add`、`git commit`、`git checkout` | 允许 | 允许 | 允许 |

网络盘符环境下的检查方式：

- 使用 IDE 语言服务（`read_lints` 工具）做静态检查，不通过命令行运行 Python 脚本；
- 通过代码审查验证逻辑正确性，不运行校验与测试；
- `read_lints` 报出的"模块找不到"等环境性错误，判断为环境问题而非代码问题，不主动修复。

## 开发命令

> 以下命令仅在满足上方"远程环境执行规则"时执行；网络盘符环境下禁止运行。

**环境准备（严格校验的前提）：**

```bash
# Linux / macOS
export PYTHONPATH=/path/to/EasyAI/backend

# Windows PowerShell
$env:PYTHONPATH = "E:\01.Projects\EasyAI\backend"

python -c "import sdk; print('sdk ok', sdk.__file__)"
```

**收录校验：**

```bash
python scripts/validate.py
# 严格模式预期：[OK] N 个插件全部通过校验（模式: sdk）
```

- 拿不到 SDK 时会降级为 `schema-only` 并打印 WARN；
  此时不检查 `IEnginePlugin` 继承与无参构造，提 PR 前必须尽量用严格模式跑一遍。
- 缺少 `jsonschema` 且缺少 SDK 时，脚本直接失败并给出二选一修复提示。

**插件自检：**

```bash
cd plugins/<plugin-id>
python <adapter>.py
# 预期输出包含 SELFTEST PASSED，并在临时目录生成可播放 WAV
```

**插件测试（可选，推荐）：**

```bash
python -m pytest plugins/<plugin-id>/test_<plugin>.py -q
# 或
python plugins/<plugin-id>/test_<plugin>.py -v
```

**索引与打包：**

```bash
python scripts/build_index.py                    # 生成 index.json（不打包）
python scripts/build_index.py --pack             # 打 zip 到 dist/ 并写 sha256
python scripts/build_index.py --pack --check     # 校验磁盘文件与生成结果是否一致
python scripts/build_index.py --pack --repo Await-d/easyai-plugins
```

**本地装进宿主：**

```bash
# 复制到插件根（目录名必须等于 plugin.id）
cp -r plugins/<plugin-id> "$EASYAI_PLUGIN_ROOT/<plugin-id>"

curl -X POST http://127.0.0.1:8000/api/plugins/install \
     -H 'Content-Type: application/json' -d '{"plugin_id":"<plugin-id>"}'
curl -X POST http://127.0.0.1:8000/api/plugins/<plugin-id>/activate
curl -X POST http://127.0.0.1:8000/api/plugins/<plugin-id>/synthesize \
     -H 'Content-Type: application/json' -d '{"text":"测试","request_id":"dev-1"}'
```

**市场索引联调：**

```bash
export EASYAI_MARKET_INDEX_URL=https://raw.githubusercontent.com/Await-d/easyai-plugins/main/index.json
curl -X POST http://127.0.0.1:8000/api/market/index/refresh
curl http://127.0.0.1:8000/api/market/index/status
```

**CI：**

- `validate.yml`：PR / push main / 手动触发；校验 manifest 与契约，
  拒绝提交构建产物，拒绝 PR 手改 `index.json`。
- `build-index.yml`：push main（`plugins/**`、脚本、工作流变更）后打包、发布
  `latest` Release、重建并提交 `index.json`。

## 测试与校验规范

- 提 PR 前必须运行 `python scripts/validate.py`，退出码为 0。
- 优先使用严格模式（能 `import sdk`）；降级模式必须在 PR 描述中主动说明，
  不得把 `schema-only` 的结果当成严格通过。
- 新增或修改插件必须运行插件自检（`python <adapter>.py`），确认真的产出音频；
  校验器只验证结构，不验证"能不能出声"。
- 鼓励为插件添加 `test_*.py`（stdlib unittest）：至少覆盖
  `initialize → load → synthesize → synthesize_stream → cancel → health → unload → shutdown`
  的生命周期冒烟。
- 测试使用系统临时目录，结束后清理；不得写入仓库、不得依赖本机固定路径。
- 测试不得 import 宿主 `main.py`；需要宿主行为时通过 HTTP API 验证。
- 环境前提不满足时如实 `skip` 并说明原因；禁止用永真断言或伪造输出伪装通过。
- manifest 必须 0 error + 0 warning；warning 不得忽略。
- 本地跑过插件后产生的 `__pycache__` / `*.pyc` 不会导致 `validate.py` 失败，
  但**禁止提交**；CI 用 `git ls-files` 判定。
- CI 目前不运行 `test_*.py`；本地测试是提交前的必要补充，不是可选项。

## 提交规范

### Git 提交规范

遵循 Conventional Commits：

```text
<type>: <subject>

<body>

<footer>
```

#### Type 类型

- `feat`: 新插件或新能力
- `fix`: 修复插件 / 脚本 / 索引逻辑
- `refactor`: 重构代码
- `chore`: 构建过程或辅助工具的变动
- `docs`: 文档更新
- `style`: 代码格式调整（不影响代码运行）
- `test`: 测试相关
- `perf`: 性能优化
- `ci`: CI / 工作流调整

#### Subject 主题

- 简洁描述本次提交的内容
- 中文或英文，保持一致
- 不超过 50 字符
- 首字母小写，结尾不加句号

#### Body 正文

- 详细描述修改内容、原因、影响
- 可以分多段
- 每行不超过 72 字符

#### Footer 页脚

- 关联的 Issue / PR 编号
- Breaking Changes 说明

### PR 流程

1. Fork → 分支 `plugin/<plugin-id>`；
2. **一插件一 PR**，便于审核；
3. PR 前跑 `python scripts/validate.py`，必须全绿；
4. **不要修改 `index.json`**；它由合并后的 CI 重建；
5. 修改已有插件必须升 `plugin.version`；
6. 维护者审核后合并，`build-index.yml` 自动打包并重建索引。

### 禁止包含的内容

1. Claude Code / AI 工具自动生成标记：

```text
Generated with [Claude Code](https://claude.com/claude-code)
```

2. AI 助手署名信息：

```text
Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

3. 其他不符合规范的内容：

- 表情符号（除非项目明确使用）
- 过度的装饰性文本
- 广告性质的链接
- 工具推广信息

### 正确的提交示例

```text
feat(plugin): add acme.whisper-tts

新增社区 TTS 插件，声明 subprocess 隔离与本地模型依赖。

修改内容：
- 新增 plugins/acme.whisper-tts/plugin.json
- 新增适配器 acme_whisper_tts.py，实现 8 个契约方法
- 补充 README 与生命周期冒烟测试

验证：
- python scripts/validate.py 全绿（模式: sdk）
- python acme_whisper_tts.py 输出 SELFTEST PASSED
```

### 提交频率

- 一个插件一个 PR；
- 修复一个 bug 提交一次；
- 不要积累太多修改再提交；
- 避免 "WIP" 或"临时提交"进入主分支。

## 配置管理

- 本仓不保存任何密钥；`.env` / `.env.local` 已被 `.gitignore` 排除。
- 关键环境变量：

| 变量 | 作用 |
|------|------|
| `PYTHONPATH` | 指向主仓 `backend/`，让 `import sdk` 可用（严格校验前提） |
| `EASYAI_CORE_TOKEN` | CI 克隆私有主仓的细粒度 PAT（仅 `Contents: Read`） |
| `EASYAI_CORE_REPO` | CI 使用的主仓仓库名，默认 `Await-d/EasyAI` |
| `EASYAI_MARKET_INDEX_URL` | 宿主拉取本仓 `index.json` 的地址 |
| `EASYAI_PLUGIN_ROOT` | 宿主插件根，默认 `<项目根>/.easyai/plugins` |
| `EASYAI_PLUGIN_ALLOW_MARKET_INSTALL` | 宿主从索引安装的显式开关，默认关闭 |

- 插件的可变配置（模型路径、采样率、参数范围）写在 `plugin.json` 或适配器常量中，
  不得依赖本机环境变量；确需环境变量时必须在 README 说明默认值与缺失行为。
- 禁止把本机绝对路径、token、真实内网地址写入插件、脚本或文档。
- `index.json` 的 `download_url` 由 `--repo` / `GITHUB_REPOSITORY` 推导，
  禁止在插件代码里硬编码仓库地址。

## 架构参考

变更前查阅：

- `README.md` — 仓库定位、宿主接入、索引生成机制、两种校验模式
- `CONTRIBUTING.md` — 收录规则、常见违规、审核红线、PR 流程
- `docs/creating-a-plugin.md` — 从零创建插件、8 个契约方法、流式与取消
- `docs/manifest-reference.md` — manifest 字段与约束
- `docs/local-development.md` — 本地开发循环、宿主内调试、市场索引联调
- `docs/troubleshooting.md` — 按症状排障
- `schema/plugin-manifest.schema.json` — manifest 唯一规范（主仓同步副本）
- 主仓 `backend/sdk/contracts.py` — 8 个契约方法与 DTO
- 主仓 `docs/plugin-dev-guide.md` — 契约语义、隔离级别、错误码

进度与知识库：

- 进度以对话与提交信息为准，不新建根目录 `todo.md`。
- 方案与任务记录优先写入 PR 描述或 `docs/`，不要创建无归属的临时 Markdown。
- 修改收录规则、schema 同步方式或打包逻辑时，必须同时更新本文件、
  `CONTRIBUTING.md` 与 CI 注释，避免规则只存在于记忆里。
