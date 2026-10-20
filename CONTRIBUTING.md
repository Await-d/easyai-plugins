# 贡献指南

感谢你为 EasyAI 贡献插件。本仓采用 **PR + 人工审核** 模式：CI 通过是必要条件，不是充分条件。

> 第一次写插件？先看 [docs/creating-a-plugin.md](./docs/creating-a-plugin.md)，
> 本文只讲**收录规则与审核标准**。

---

## 0. 提交前必做

```bash
python scripts/validate.py
```

全绿才能提 PR。

需要能 `import sdk` 才是**严格模式**：

```bash
export PYTHONPATH=/path/to/EasyAI/backend   # Windows: $env:PYTHONPATH="E:\...\EasyAI\backend"
```

拿不到 SDK 时会降级为 `schema-only`（打印 WARN）：仍会校验 schema 与 8 个契约方法名，
但**不检查继承与无参构造**。提 PR 前请尽量用严格模式跑一遍。

---

## 1. 新增一个插件

1. **建目录**：`plugins/<publisher>.<plugin-id>/`

   目录名**必须等于** `plugin.json` 里的 `plugin.id`。CI 会硬性检查 ——
   不一致时打包文件名与安装目标目录会错位，而这类错误在本地极难发现。

2. **写 `plugin.json`**：以 `plugins/community.example-tone/plugin.json` 为起点改。

   必填：`plugin.id` / `name` / `version` / `engine_kind` / `api_version` / `entry_point`。

3. **实现适配器**：继承 `sdk.IEnginePlugin`，实现 **8 个方法**：

   `initialize` / `load` / `unload` / `synthesize` / `synthesize_stream` / `cancel` / `health` / `shutdown`

   三条硬约束：
   - **必须能无参构造**（宿主按 `entry_point` 直接 `Cls()`，不会传任何参数）；
   - **模块名全局唯一**（不要叫 `adapter.py` —— 仓内历史上三个插件同名，
     `sys.modules` 按名缓存导致后加载的拿到前一个的类）；
   - `unload` / `shutdown` **必须幂等**。

4. **写 `README.md`**（必需）：说明插件做什么、依赖什么、如何获取模型、
   已知限制。**不要**声称未验证过的能力。

5. **加测试**（强烈建议）：至少一条生命周期冒烟（`initialize → load → synthesize`）。

6. 跑 `python scripts/validate.py`，全绿后提 PR。

---

## 2. 修改已有插件

- **必须升 `plugin.version`**（严格三段 `x.y.z`）。宿主的升级/回滚按版本号工作，
  版本不变会让已安装用户永远收不到更新。
- 不兼容变更（改了 `entry_point`、删了能力、改了模型 id）请在 PR 描述里写明，
  并考虑升主版本号。

---

## 3. 常见违规（都是本仓真实踩过的坑）

| 现象 | 原因 |
|---|---|
| `manifest error: 不允许的额外属性` | 顶层或分段多写了字段。例如 `isolation.venv` 是**多余属性**，venv 由宿主管理 |
| `permissions.network` 报类型错 | 它必须是**域名数组**（如 `["api.example.com"]`），写 `true` 会失败 |
| `models.explicit[].name` 缺失 | 字段名是 `name`，**不是** `model_id` |
| `version` 校验失败 | 必须严格三段 `1.2.3`，`1.2` 会被拒 |
| `api_version` 校验失败 | 只支持 `>= <= == != > < =` 组合，`~>1.0` 不支持 |
| `entry_point` 校验失败 | 只能 `module:Class` 形式，且 module 必须是合法 Python 标识符（不能有 `-`） |
| `契约方法缺失` | 8 个方法缺一不可，注意 `synthesize_stream` 要是 `async def` + `yield` |
| `无参构造失败` | 在 `__init__` 里加了必填参数，或构造时就加载了重依赖 |
| 插件里出现 `__pycache__` | 请提交前清理，`.gitignore` 已覆盖 |

---

## 4. 审核要点（安全红线）

以下情况**直接拒绝**，不接受讨论：

1. **联网回连**：除声明在 `permissions.network` 中的域名外，不得向任何地址发起请求；
2. **读取插件目录外的文件**：不得读用户主目录、系统目录、其他插件目录；
3. **执行外部命令 / 动态 `eval`**：不得 `subprocess` 起任意进程，不得 `eval`/`exec` 远程内容；
4. **打包二进制**：不得在仓库内提交 `.exe`/`.dll`/`.so`/`.pyd` 等预编译产物；
5. **挖矿 / 代理 / 凭据收集**：与插件功能无关的任何后台活动；
6. **隐藏行为**：`plugin.json` 声明的能力必须与实现一致（声明无网络却发请求，视为恶意）。

另外：

- `post_install` 脚本会被逐一审查，能不用就不用；
- 依赖尽量少，且必须是 PyPI 上可核实的包名；
- 模型文件**不要**进仓库（用 `models.explicit[].url` 指向官方发布地址）。

---

## 5. PR 流程

1. Fork → 分支 `plugin/<plugin-id>`；
2. 提交：一插件一 PR，便于审核；
3. CI 必须绿（`validate.yml`）；
4. **不要修改 `index.json`** —— 它是生成物，PR 里改了会被 CI 直接拒绝；
5. 维护者审核后合并，`build-index.yml` 会自动打包、发布 Release、重建索引。

---

## 6. 关于签名

索引里的 `sha256` 目前用于**完整性校验**，不是来源签名。

宿主侧的签名验证与权限沙箱尚未落地（主仓 T-21，条件触发）。
在它落地前，请把本仓当作**半可信来源**：只安装你愿意在宿主机上运行的代码。
