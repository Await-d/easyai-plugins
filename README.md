# EasyAI Community Plugins

EasyAI 引擎插件的**社区仓库**。与主仓 [EasyAI](https://github.com/Await-d/EasyAI) 分离：

- 主仓负责**宿主 + SDK 契约 + 内置引擎插件**（`backend/plugins/builtin/`）；
- 本仓负责**社区插件**的收录、校验、打包与索引分发。

这样插件可以独立迭代、独立审核，不必每次改动都触发宿主 CI；宿主也能保持"最小可信内核"。

> **本地布局说明**：本仓在开发机上检出到主仓根目录的 `easyai-plugins/` 下，
> 便于一起打开与调试；但它是**独立 git 仓库**（有自己的远端、CI 与版本历史），
> 主仓已用 `.gitignore` 排除该目录，**不会**把这里的文件纳入主仓版本控制。

---

## 宿主如何消费本仓

宿主已支持**从索引直接安装**，但**默认关闭**——「从公网下载并执行第三方代码」必须显式启用：

```bash
# 1) 指向本仓索引
export EASYAI_MARKET_INDEX_URL=https://raw.githubusercontent.com/Await-d/easyai-plugins/main/index.json

# 2) 显式开启市场安装（默认 false）
export EASYAI_PLUGIN_ALLOW_MARKET_INSTALL=true
```

开启后，在市场里点安装，宿主会依次：**下载 zip → 校验 sha256 → 安全检查 → 解压到插件根 → 走本地安装流程**。

| 能力 | 状态 |
|---|---|
| 拉取并展示远程索引 | ✅ 已有（`EASYAI_MARKET_INDEX_URL`） |
| 索引格式校验 / 缓存 / 离线降级 | ✅ 已有 |
| 从**本地插件根**安装、激活、合成 | ✅ 完整 |
| 从索引**下载 / sha256 校验 / 安全解压** | ✅ 已有（需开启 `EASYAI_PLUGIN_ALLOW_MARKET_INSTALL`） |
| 来源签名验证 | ❌ 属后续（主仓 T-21） |

宿主侧的安全约束（任一不满足即拒绝安装，返回 422 + `error_code`）：

- 条目**必须提供 sha256**，且必须匹配——没有摘要就不装；
- 只允许 `https` / `file`，明文 `http` 拒绝；
- 拒绝 zip-slip（`../`、绝对路径、盘符）、符号链接、条目数/解压体积超限；
- 包内 `plugin.id` 必须与请求一致；
- **目标目录已存在则不覆盖**——升级走 `/upgrade` 通道（蓝绿切换，失败可回滚）。

> 也可以继续手动安装（下载 Release 里的 zip → 解压到 `<插件根>/<plugin_id>/` → 调 install）。
> 手动方式不经过上述下载校验，也不受开关限制。

---

## 文档导航

| 我想…… | 看这篇 |
|---|---|
| **第一次写插件** | [docs/creating-a-plugin.md](./docs/creating-a-plugin.md) —— 从复制示例到宿主内跑通合成 |
| 查 `plugin.json` 字段与约束 | [docs/manifest-reference.md](./docs/manifest-reference.md) —— 含逐条报错含义 |
| 本地反复调试、最后发布 | [docs/local-development.md](./docs/local-development.md) |
| 出错了 | [docs/troubleshooting.md](./docs/troubleshooting.md) —— 按症状查 |
| 提交规范与审核红线 | [CONTRIBUTING.md](./CONTRIBUTING.md) |

## 5 分钟上手

```bash
# 0) 让 SDK 可导入（严格校验模式的前提）
export PYTHONPATH=/path/to/EasyAI/backend

# 1) 复制示例插件，目录名就是将来的 plugin.id
cp -r plugins/community.example-tone plugins/acme.whisper-tts

# 2) 改 4 处：
#    plugin.json 的 id / name / entry_point
#    适配器文件名（改成全局唯一的模块名，如 acme_whisper_tts.py）
#    适配器里的类名（与 entry_point 冒号后一致）
#    synthesize 的实现（换成你的推理调用，必须回 audio_path）

# 3) 校验（数量随 plugins/ 下插件数变化）
python scripts/validate.py
# 预期：[OK] N 个插件全部通过校验（模式: sdk）

# 4) 脱离宿主自检
cd plugins/acme.whisper-tts && python acme_whisper_tts.py
# 预期：... SELFTEST PASSED
```

想立刻看到"装进宿主、激活、合成出音频"的完整链路？
跟 [docs/creating-a-plugin.md](./docs/creating-a-plugin.md) 的第 5 节走一遍。

## 目录结构

```
.
├── index.json                  # ⚠️ 生成物，禁止手改（CI 重建）
├── plugins/
│   └── <publisher>.<plugin-id>/ # 目录名必须等于 plugin.json 的 plugin.id
│       ├── plugin.json
│       ├── <adapter>.py         # 模块名必须全局唯一
│       ├── README.md            # 必需
│       └── test_*.py            # 可选，鼓励
├── schema/
│   └── plugin-manifest.schema.json  # 从主仓同步，勿单独修改
├── scripts/
│   ├── validate.py              # 收录校验（CI 与本地共用）
│   └── build_index.py           # 生成 index.json（唯一产出方式）
└── .github/workflows/
    ├── validate.yml             # PR 校验
    └── build-index.yml          # 合并后打包 + 重建索引
```

---

## 让宿主指向本仓库的索引

设置环境变量（或写进部署配置）：

```bash
EASYAI_MARKET_INDEX_URL=https://raw.githubusercontent.com/Await-d/easyai-plugins/main/index.json
```

刷新索引：

```bash
curl -X POST http://127.0.0.1:8000/api/market/index/refresh
curl http://127.0.0.1:8000/api/market/index/status
```

> `index.json` 必须先由 CI 生成（合并到 `main` 后自动完成）。首次建仓时本地跑一次
> `python scripts/build_index.py --pack --repo Await-d/easyai-plugins` 也可以。

---

## 手动安装一个插件

1. 从 [Releases](https://github.com/Await-d/easyai-plugins/releases/tag/latest) 下载 `<plugin_id>-<version>.zip`；
2. 解压到插件根下、**以 `plugin_id` 命名**的目录：

   ```
   <插件根>/<plugin_id>/plugin.json
   <插件根>/<plugin_id>/<adapter>.py
   ```

   插件根默认是 `<项目根>/.easyai/plugins`，可用 `EASYAI_PLUGIN_ROOT` 覆盖。
3. 调用安装接口：

   ```bash
   curl -X POST http://127.0.0.1:8000/api/plugins/install \
        -H 'Content-Type: application/json' \
        -d '{"plugin_id": "community.example-tone"}'
   ```

4. 激活并试听：

   ```bash
   curl -X POST http://127.0.0.1:8000/api/plugins/community.example-tone/activate
   ```

---

## 索引是怎么生成的

**永远不要手改 `index.json`。** 它由 `scripts/build_index.py` 从 `plugins/*/plugin.json` 聚合出来：

- 每个插件目录打成 **可复现的 zip**（固定时间戳、固定压缩级别、文件排序），
  因此内容不变时 sha256 也不变；
- 填充 `download_url`（指向 `latest` Release）与 `sha256`；
- 内嵌完整 `manifest`，宿主无需再下载 `plugin.json` 即可判定可安装性；
- 带出 `api_version`（宿主契约兼容区间）：市场可以在**下载之前**就提示
  "此插件要求更高版本的宿主"，而不是等用户装完、激活时才失败。

本地重建：

```bash
python scripts/validate.py                          # 先校验
python scripts/build_index.py --pack --repo Await-d/easyai-plugins
```

---

## 校验的两种模式

| 模式 | 前提 | 覆盖范围 |
|---|---|---|
| **`sdk`**（严格） | `import sdk` 可用（主仓 `backend/` 在 `PYTHONPATH`） | schema + `IEnginePlugin` 继承 + 8 方法 + **无参构造** |
| **`schema-only`**（降级） | 拿不到 SDK，但有 `jsonschema` | 本仓 schema 副本 + **AST** 检查 8 个方法名 |

拿不到 SDK 时脚本会**打印 WARN 并降级**，不会伪装成严格模式；
降级模式无法验证「继承了 `IEnginePlugin`」和「可无参构造」。

主仓是**私有仓库**，因此 CI 无法匿名 clone。要启用严格模式，请在
`Settings → Secrets and variables → Actions` 添加 **`EASYAI_CORE_TOKEN`**
（细粒度 PAT，仅需对 `Await-d/EasyAI` 的 `Contents: Read`）。

### 每个插件的硬性要求

`scripts/validate.py` 会逐条检查；CI 与本地跑的是同一份脚本。

| 要求 | 为什么 | 在降级模式也检查？ |
|---|---|---|
| `README.md` **与** `LICENSE` | 收录第三方代码必须有许可证声明 | ✅ |
| `LICENSE` 是**完整文本** | 只写一行 `MIT` 等于没写 | ✅ |
| 目录名 == `plugin.id` | 打包文件名与安装目标目录都依赖它 | ✅ |
| `plugin.api_version` 能被**当前宿主**满足 | 不满足 = 用户装上也用不了 | ❌ 需 SDK |
| 改了 `plugin.json` / `*.py` 必须 **bump `version`** | 版本号不变而 sha256 变了，升级检测与回滚同时失效 | ✅（PR 上） |
| 不得修改 `schema/` | 它是主仓副本，漂移会让 PR 全绿但装不上 | ❌ 需能读到主仓 |

## 贡献

见 [CONTRIBUTING.md](./CONTRIBUTING.md)。核心要求一句话：**`python scripts/validate.py` 全绿**。

---

## 许可证

本仓自身的脚本与文档采用 MIT。

**每个插件目录必须自带 `LICENSE`**（完整许可证文本，不是只写一行 `MIT`），
以该文件为准 —— 收录第三方代码却没有许可证声明是法律风险，校验器会拒绝。
