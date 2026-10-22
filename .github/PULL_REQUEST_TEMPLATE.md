## 这个 PR 做了什么

<!-- 一句话说清：新增插件 / 修插件 bug / 改校验脚本 / 改文档 -->

## 涉及的插件

<!-- 新增或修改了哪个 plugin_id？改文档/脚本可填「无」 -->

- `plugin_id`：
- `version`（改动前后）：

## 自检清单

请逐项确认，**没勾的项审核会退回**：

- [ ] `python scripts/validate.py` 本地全绿（贴最后一行输出）
- [ ] 若改了 `plugin.json` 或 `*.py`，**已 bump `version`**（CI 会检查）
- [ ] 插件目录内已有 `README.md` 与 `LICENSE`（LICENSE 是完整文本，不是只写 `MIT`）
- [ ] `plugin.api_version` 能被当前宿主 SDK 满足（校验器会判定）
- [ ] **没有**修改 `index.json`（它是生成物，CI 自动重建）
- [ ] **没有**提交 `__pycache__` / `*.pyc` / `.venv`
- [ ] 没有顶层 `__init__.py`（会把插件目录变成包）
- [ ] 若新增依赖，已写进 `plugin.json` 的 `dependencies.python_packages`
- [ ] 不在本仓 `schema/` 里改规则（它是主仓副本，改了会被一致性检查拦下）

## 本地校验输出

```
$ python scripts/validate.py
<粘贴输出>
```

## 给审核者的额外说明

<!-- 例如：插件调用了哪些外部服务、是否需要 GPU、是否有已知限制 -->
