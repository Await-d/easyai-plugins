# 文档导航

本目录是**面向插件作者**的文档。仓库级说明（这个仓是什么、索引怎么工作、宿主如何接入）
见根目录 [README.md](../README.md)。

| 文档 | 你在什么时候看它 |
|---|---|
| [creating-a-plugin.md](./creating-a-plugin.md) | **第一次写插件**。从复制示例到跑通合成，一步一命令 |
| [manifest-reference.md](./manifest-reference.md) | 写 `plugin.json` 时查字段、查约束、查报错含义 |
| [local-development.md](./local-development.md) | 本地反复调试、不发布也能测、最后怎么发出去 |
| [troubleshooting.md](./troubleshooting.md) | 校验不过 / 激活失败 / 合成没声音 / 索引拉不到 |

## 三条最容易踩的坑（先记住）

1. **`plugin.id` 必须含点**，且**目录名必须等于它**。少了点或目录名不一致都会被拒。
2. **适配器模块名必须全局唯一**。不要叫 `adapter.py` —— 宿主按模块名缓存，
   多个插件同名会互相串类（本仓历史上真实踩过）。
3. **`__init__` 不能有必填参数**。宿主按 `entry_point` 直接 `Cls()` 构造，
   加参数会让 `activate` 失败，而校验器只能在没有 SDK 时静态检查，容易漏。

## 权威来源

本文档与以下文件必须保持一致，冲突时以**代码与 schema** 为准：

- `schema/plugin-manifest.schema.json` —— manifest 的唯一规范
- 主仓 `backend/sdk/contracts.py` —— 8 个契约方法
- 主仓 `docs/plugin-dev-guide.md` —— 更完整的契约语义与设计取舍
