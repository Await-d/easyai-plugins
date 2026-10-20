# 本地开发与发布

本文讲**日常怎么迭代**：改代码、快速验证、不发布也能在宿主里跑、最后怎么发出去。

---

## 1. 环境准备

只需要一件事：让 `sdk` 可导入。

```bash
export PYTHONPATH=/path/to/EasyAI/backend          # Linux / macOS
$env:PYTHONPATH="E:\01.Projects\EasyAI\backend"    # Windows PowerShell
```

验证：

```bash
python -c "import sdk; print(sdk.__file__)"
```

> 不设 `PYTHONPATH` 也能跑校验，但会降级为 `schema-only`：
> 不检查「继承 `IEnginePlugin`」和「可无参构造」，容易放过真实缺陷。

---

## 2. 开发循环

推荐每次改动都跑这一轮，成本很低：

```bash
# ① 静态校验（秒级）
python scripts/validate.py

# ② 插件自检（不启动宿主，能抓 80% 的错误）
cd plugins/<你的id> && python <adapter>.py && cd -

# ③ 需要真实宿主行为时，再放到插件根（见下）
```

**不要跳过 ②**：它能验证"真的产出了音频"，而校验器只验证结构。

---

## 3. 不发布也能在宿主里测

宿主**支持**从索引安装（需开启 `EASYAI_PLUGIN_ALLOW_MARKET_INSTALL`），
但本地迭代仍推荐"直接把目录放进去"：不必等 CI 打包，也不会被 sha256 校验
拦住你还没发布的改动。

### 3.1 找到插件根

默认 `<项目根>/.easyai/plugins`，可用环境变量覆盖：

```bash
echo $EASYAI_PLUGIN_ROOT
# 未设置时用默认值
```

### 3.2 放进去（目录名 = plugin.id）

```bash
cp -r plugins/<你的id> "$EASYAI_PLUGIN_ROOT/<你的id>"
```

Windows：

```powershell
Copy-Item -Recurse "plugins\<你的id>" "$env:EASYAI_PLUGIN_ROOT\<你的id>"
```

> 若插件根不存在，先创建。**目录名写错**会导致 `install` 报 404
> （宿主按 `plugin_id` 找目录）。

### 3.3 走 API

```bash
# 安装
curl -X POST localhost:8000/api/plugins/install \
     -H 'Content-Type: application/json' -d '{"plugin_id":"<你的id>"}'

# 激活
curl -X POST localhost:8000/api/plugins/<你的id>/activate

# 合成（带 request_id 才会有 audio_url）
curl -X POST localhost:8000/api/plugins/<你的id>/synthesize \
     -H 'Content-Type: application/json' \
     -d '{"text":"测试","request_id":"dev-1"}'

# 健康
curl localhost:8000/api/plugins/<你的id>/health
```

### 3.4 改了代码怎么生效

宿主持有运行时句柄，**改文件不会自动重载**：

```bash
curl -X POST localhost:8000/api/plugins/<你的id>/deactivate
# ... 改代码、重新复制到插件根 ...
curl -X POST localhost:8000/api/plugins/<你的id>/activate
```

`in_process` 模式下若插件已把模块载入 `sys.modules`，**可能需要重启宿主**才能
拿到新代码。这是本地开发优先用 `subprocess` 的一个理由。

---

## 4. 让宿主显示本仓的市场列表

想验证"市场里能不能看到"，设置索引地址：

```bash
export EASYAI_MARKET_INDEX_URL=https://raw.githubusercontent.com/Await-d/easyai-plugins/main/index.json

curl -X POST localhost:8000/api/market/index/refresh
curl localhost:8000/api/market/index/status
```

`status` 里关注 `degraded` 与 `degrade_reason`：

- `degraded=false` → 拉到了远端
- `degraded=true` → 走的是缓存或空列表，原因在 `degrade_reason`

本地调试索引时可以直接指 `file://`：

```bash
export EASYAI_MARKET_INDEX_URL=file:///E:/01.Projects/EasyAI/easyai-plugins/index.json
```

> Windows 上 `file://` 必须是**三斜杠** `file:///E:/...`，否则盘符会丢。

---

## 5. 调试技巧

| 想知道 | 怎么做 |
|---|---|
| 宿主持有句柄了吗 | `GET /api/plugins/{id}/capabilities`，看 `has_instance` / `backend_info` |
| 当前状态与历史 | `GET /api/plugins/{id}/health`，看 `state` 与 `history` |
| 插件报了什么错 | 合成失败时看响应 `detail.reasons`（含插件侧原始错误） |
| 依赖装了吗 | 看 `<插件根>/<id>/.venv/` 是否存在 |
| 音频在哪 | 合成响应的 `audio_path`（仅排障用，别给前端拼路径） |

常见错误码：

| 码 | 含义 |
|---|---|
| 404 | 插件不在插件根（目录名与 id 不一致最常见） |
| 409 | 未激活，或状态不允许当前操作 |
| 422 | manifest 校验失败 / 硬件硬约束不满足 / 请求参数非法 |
| 502 | 插件内部执行失败（`detail.error_code` 是插件侧原始码） |
| 503 | 子进程崩溃 / 重启耗尽 / 心跳超时（可重试） |

---

## 6. 发布

```bash
python scripts/validate.py           # 必须全绿
git checkout -b plugin/<你的id>
git add plugins/<你的id>
git commit -m "feat(plugin): add <你的id>"
git push -u origin plugin/<你的id>
gh pr create --fill
```

合并到 `main` 后 CI 自动：

1. 校验；
2. 打可复现 zip（固定时间戳、固定 `create_system`、CRLF→LF）；
3. 上传到 `latest` Release；
4. 重建并提交 `index.json`。

### 为什么 sha256 必须稳定

`index.json` 里的 `sha256` 用于完整性校验。打包若不可复现（例如未固定
`ZipInfo.create_system`），Windows 与 Linux 会打出不同字节，导致
"本地生成的索引与 CI 生成的对不上"，且**用户下载后校验会失败**。

本仓已固定这些变量，不要再改动 `build_index.py` 的打包细节而不跑一致性检查：

```bash
python scripts/build_index.py --pack --repo Await-d/easyai-plugins
python scripts/build_index.py --pack --repo Await-d/easyai-plugins --check
```

第二条必须输出 `[OK] index.json 已是最新`。
