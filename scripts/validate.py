#!/usr/bin/env python3
"""校验 ``plugins/`` 下的插件是否满足**收录要求**（CI 与本地共用）。

检查项
------
1. ``plugin.json`` 通过 SDK 的 schema 校验（**零 error**）；
2. 目录名 == ``plugin.id``（不一致会导致打包文件名与安装目标目录错位）；
3. ``entry_point`` 可解析：模块文件存在、类可导入、是 ``IEnginePlugin`` 子类；
4. 契约 **8 个方法**齐备，且类可**无参构造**（宿主就是这么构造它的）；
5. 每个插件目录有 ``README.md``；
6. 插件目录内不得出现顶层 ``__init__.py``（会让插件目录变成包，与宿主的
   "加 sys.path 后按模块名导入" 约定冲突）。

> ``__pycache__`` / ``*.pyc`` **不在此检查**：本地跑过一次就会出现，
> 逐次报错只会制造假失败。是否被误提交由 CI 的 ``git ls-files`` 判定。

退出码：0 全通过 / 1 有问题。

为什么用唯一的 ``spec_from_file_location`` 模块名
------------------------------------------------
本仓库历史上三个内置插件都叫 ``adapter.py``，``sys.modules`` 按名缓存导致后加载的
拿到前一个的类。校验器若用普通 ``import``，同样会在多个插件间串味 ——
故每个插件都用一个带前缀的唯一模块名加载。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

PLUGINS_DIRNAME = "plugins"
MANIFEST_FILENAME = "plugin.json"
REQUIRED_FILES = ("README.md",)

#: 构建产物目录（由 .gitignore 覆盖；CI 用 ``git ls-files`` 检查是否被误提交）。
FORBIDDEN_DIRS = {"__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache"}


class Reporter:
    """收集问题并按插件分组输出（一次性给全，避免改一个报一个）。"""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def fail(self, plugin: str, message: str) -> None:
        self.problems.append(f"{plugin}: {message}")

    def ok(self) -> bool:
        return not self.problems

    def dump(self) -> None:
        for item in self.problems:
            print(f"[FAIL] {item}", file=sys.stderr)


#: 降级模式下使用的契约方法名（与主仓 `sdk.contracts.REQUIRED_PLUGIN_METHODS` 对齐）。
#: 只有在拿不到 SDK 时才会用到；**改契约时必须同步这里**。
FALLBACK_REQUIRED_METHODS = (
    "initialize",
    "load",
    "unload",
    "synthesize",
    "synthesize_stream",
    "cancel",
    "health",
    "shutdown",
)

SCHEMA_PATH = Path("schema") / "plugin-manifest.schema.json"


class _ManifestResult:
    """与 ``sdk.manifest_validator`` 返回值形状兼容的最小结果对象。"""

    def __init__(self, errors: list[str], manifest: dict | None) -> None:
        self.errors = errors
        self.warnings: list[str] = []
        self.manifest = manifest


def _jsonschema_validator():
    """用本仓 ``schema/`` 构造降级校验器（不依赖主仓）。"""
    try:
        import jsonschema
    except ImportError:
        print(
            "[FAIL] 既拿不到 EasyAI SDK，也没有 jsonschema 包，无法校验 manifest。\n"
            "       二选一：\n"
            "       1) 让主仓的 SDK 可用：export PYTHONPATH=/path/to/EasyAI/backend\n"
            "       2) pip install jsonschema",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not SCHEMA_PATH.is_file():
        print(f"[FAIL] 缺少 {SCHEMA_PATH}", file=sys.stderr)
        raise SystemExit(1)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def _validate(manifest_path: str) -> _ManifestResult:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        try:
            jsonschema.validate(payload, schema)
        except jsonschema.ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
            return _ManifestResult(errors=[f"{exc.message}（位置: {location}）"], manifest=None)
        return _ManifestResult(errors=[], manifest=payload)

    return _validate


def _load_validation_backends():
    """优先用主仓 SDK（严格），否则降级为「本仓 schema + 方法名」校验。

    返回 ``(validate_manifest_file, contract_or_none, required_methods, mode)``。

    ⚠️ 降级模式**不会**检查 ``issubclass(IEnginePlugin)``（没有基类可比），
    且 schema 是仓库内副本，可能与主仓最新版有偏差。因此降级时**必须**
    在输出里显式警告，不能让它看起来跟严格模式一样。
    """
    try:
        from sdk.contracts import IEnginePlugin, REQUIRED_PLUGIN_METHODS
        from sdk.manifest_validator import validate_manifest_file

        return validate_manifest_file, IEnginePlugin, REQUIRED_PLUGIN_METHODS, "sdk"
    except ImportError as exc:  # pragma: no cover - 取决于环境
        print(
            f"[WARN] 未找到 EasyAI SDK（{exc.__class__.__name__}），"
            "降级为 schema-only 校验：\n"
            "       · manifest 用本仓 schema/ 副本校验（可能与主仓最新版有偏差）\n"
            "       · 契约只检查 8 个方法名，**跳过** IEnginePlugin 继承检查\n"
            "       要恢复严格模式：export PYTHONPATH=/path/to/EasyAI/backend\n"
            "       或在 CI 配置 secret EASYAI_CORE_TOKEN（主仓为私有仓库时必需）\n",
            file=sys.stderr,
        )
        return _jsonschema_validator(), None, FALLBACK_REQUIRED_METHODS, "schema-only"


def _resolve_module_file(plugin_dir: Path, module: str) -> Path | None:
    """把 ``entry_point`` 的模块名映射到文件路径（支持 ``a.b`` 子包写法）。"""
    relative = Path(*module.split("."))
    for candidate in (
        plugin_dir / f"{relative}.py",
        plugin_dir / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _static_contract_check(
    module_file: Path,
    class_name: str,
    required_methods: tuple[str, ...],
    reporter: Reporter,
    label: str,
) -> None:
    """不导入模块，直接看 AST 里该类是否实现了全部契约方法。

    只能在降级模式用：它证明"方法写了"，但证明不了"方法有效"、
    也证明不了"继承了 IEnginePlugin"或"可无参构造"。
    """
    try:
        tree = ast.parse(module_file.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        reporter.fail(label, f"语法错误（{module_file.name}）: {exc.msg} (line {exc.lineno})")
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            defined = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            missing = [name for name in required_methods if name not in defined]
            if missing:
                reporter.fail(label, f"{class_name} 缺少契约方法: {missing}")
            return

    reporter.fail(label, f"模块 {module_file.name} 里没有类 {class_name}")


def _check_entry_point(
    plugin_dir: Path,
    entry_point: str,
    *,
    contract: type | None,
    required_methods: tuple[str, ...],
    reporter: Reporter,
) -> None:
    label = plugin_dir.name
    module_name, sep, class_name = entry_point.partition(":")
    if not sep or not module_name or not class_name:
        reporter.fail(label, f"entry_point 必须是 'module:Class' 形式，实际 {entry_point!r}")
        return

    module_file = _resolve_module_file(plugin_dir, module_name)
    if module_file is None:
        reporter.fail(label, f"entry_point 指向的模块不存在: {module_name}")
        return

    # 降级模式：插件必然 `import sdk`，没有 SDK 时**导入一定失败**。
    # 此时改用 AST 静态检查方法名 —— 不依赖 import，仍能挡住"漏写方法"这一最常见问题。
    # （继承关系与"无参可构造"无法静态确认，已在降级 WARN 中声明）
    if contract is None:
        _static_contract_check(module_file, class_name, required_methods, reporter, label)
        return

    # 唯一模块名：防止多个插件同名模块在 sys.modules 里串味
    unique_name = f"_easyai_validate_{abs(hash((str(plugin_dir), module_name)))}"
    spec = importlib.util.spec_from_file_location(unique_name, module_file)
    if spec is None or spec.loader is None:
        reporter.fail(label, f"无法加载模块: {module_file}")
        return

    module = importlib.util.module_from_spec(spec)
    inserted = str(plugin_dir)
    added = inserted not in sys.path
    if added:
        sys.path.insert(0, inserted)
    # 必须注册到 sys.modules 再 exec：模块内的 dataclass / typing / 自引用
    # （``sys.modules[__name__]``）都依赖 ``module.__spec__`` 能查到自己。
    # 不注册时 exec_module 会抛 ``'NoneType' object has no attribute '__dict__'``。
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 插件导入失败就是不合规
        reporter.fail(label, f"导入模块失败（{module_name}）: {type(exc).__name__}: {exc}")
        return
    finally:
        sys.modules.pop(unique_name, None)
        if added:
            try:
                sys.path.remove(inserted)
            except ValueError:
                pass

    cls = getattr(module, class_name, None)
    if cls is None:
        reporter.fail(label, f"模块 {module_name} 里没有类 {class_name}")
        return
    if not isinstance(cls, type):
        reporter.fail(label, f"{class_name} 不是类")
        return
    if contract is not None and not issubclass(cls, contract):
        reporter.fail(label, f"{class_name} 未继承 IEnginePlugin")
        return

    missing = [name for name in required_methods if not callable(getattr(cls, name, None))]
    if missing:
        reporter.fail(label, f"契约方法缺失: {missing}")
        return

    # 宿主按 entry_point **无参构造**；任何必填构造参数都会让 activate 失败
    try:
        cls()
    except Exception as exc:  # noqa: BLE001
        reporter.fail(label, f"{class_name}() 无参构造失败: {type(exc).__name__}: {exc}")


def _check_files(plugin_dir: Path, reporter: Reporter) -> None:
    label = plugin_dir.name
    for name in REQUIRED_FILES:
        if not (plugin_dir / name).is_file():
            reporter.fail(label, f"缺少 {name}")

    # 注意：这里**不**扫描 ``__pycache__`` / ``*.pyc``。
    # 它们是本地跑过一次就会出现的运行时产物（`.gitignore` 已覆盖），
    # 若在此报错，贡献者每次本地自检后都会看到假失败。
    # "是否被误提交"交给 CI 用 ``git ls-files`` 判定（见 validate.yml）。

    if (plugin_dir / "__init__.py").is_file():
        reporter.fail(
            label,
            "插件目录内不应有顶层 __init__.py（会把目录变成包，与宿主按模块名导入的约定冲突）",
        )


def validate_plugin(
    plugin_dir: Path,
    *,
    contract: type | None,
    required_methods: tuple[str, ...],
    validate_manifest_file,
    reporter: Reporter,
) -> None:
    label = plugin_dir.name

    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        reporter.fail(label, f"缺少 {MANIFEST_FILENAME}")
        return

    result = validate_manifest_file(str(manifest_path))
    for error in result.errors:
        reporter.fail(label, f"manifest error: {error}")
    for warning in result.warnings:
        # warning 不阻塞合并，但必须让贡献者看到（零 warning 是本仓的既定要求）
        print(f"[WARN] {label}: {warning}")
    if result.errors:
        return

    manifest = result.manifest
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    section = payload.get("plugin") or {}
    plugin_id = str(section.get("id") or "")

    if plugin_dir.name != plugin_id:
        reporter.fail(label, f"目录名必须等于 plugin.id（目录={label!r} id={plugin_id!r}）")

    # 索引条目规则：plugin_id 不要求含点，但插件 id 必须含点（由 schema 保证）
    if not plugin_id:
        reporter.fail(label, "plugin.id 为空")
    else:
        try:
            index_module = importlib.import_module("scripts.build_index")
        except ImportError:
            index_module = None
        if index_module is not None and not index_module.REQUIRED_PLUGIN_FIELDS:
            reporter.fail(label, "内部错误：REQUIRED_PLUGIN_FIELDS 为空")

    entry_point = str(section.get("entry_point") or "")
    if entry_point:
        _check_entry_point(
            plugin_dir,
            entry_point,
            contract=contract,
            required_methods=required_methods,
            reporter=reporter,
        )
    else:
        reporter.fail(label, "plugin.entry_point 为空")

    _check_files(plugin_dir, reporter)

    # 确保 payload 至少被解析过（避免"看起来通过但其实没读文件"）
    del manifest


def main() -> int:
    validate_manifest_file, contract, required_methods, mode = _load_validation_backends()

    plugins_root = Path(PLUGINS_DIRNAME)
    if not plugins_root.is_dir():
        print(f"[FAIL] 找不到 {PLUGINS_DIRNAME}/ 目录", file=sys.stderr)
        return 1

    plugin_dirs = sorted(
        p for p in plugins_root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    )
    if not plugin_dirs:
        print(f"[FAIL] {PLUGINS_DIRNAME}/ 下没有任何插件", file=sys.stderr)
        return 1

    reporter = Reporter()
    seen: dict[str, str] = {}
    for plugin_dir in plugin_dirs:
        validate_plugin(
            plugin_dir,
            contract=contract,
            required_methods=required_methods,
            validate_manifest_file=validate_manifest_file,
            reporter=reporter,
        )
        manifest_path = plugin_dir / MANIFEST_FILENAME
        if manifest_path.is_file():
            try:
                plugin_id = str(
                    (json.loads(manifest_path.read_text(encoding="utf-8")).get("plugin") or {}).get("id")
                    or ""
                )
            except ValueError:
                plugin_id = ""
            if plugin_id:
                if plugin_id in seen:
                    reporter.fail(
                        plugin_dir.name, f"plugin.id 重复: {plugin_id}（另一处 {seen[plugin_id]}）"
                    )
                seen[plugin_id] = plugin_dir.name

    if not reporter.ok():
        reporter.dump()
        print(f"\n[FAIL] 共 {len(reporter.problems)} 个问题", file=sys.stderr)
        return 1

    print(f"[OK] {len(plugin_dirs)} 个插件全部通过校验（模式: {mode}）")
    for plugin_dir in plugin_dirs:
        print(f"     - {plugin_dir.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
