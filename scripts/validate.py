#!/usr/bin/env python3
"""校验 ``plugins/`` 下的插件是否满足**收录要求**（CI 与本地共用）。

检查项
------
1. ``plugin.json`` 通过 SDK 的 schema 校验（**零 error**）；
2. 目录名 == ``plugin.id``（不一致会导致打包文件名与安装目标目录错位）；
3. ``entry_point`` 可解析：模块文件存在、类可导入、是 ``IEnginePlugin`` 子类；
4. 契约 **8 个方法**齐备，且类可**无参构造**（宿主就是这么构造它的）；
5. 每个插件目录有 ``README.md`` 与 ``LICENSE``（**且 LICENSE 非空** ——
   收录第三方代码没有许可证声明是法律风险，不是风格问题）；
6. 插件目录内不得出现顶层 ``__init__.py``（会让插件目录变成包，与宿主的
   "加 sys.path 后按模块名导入" 约定冲突）；
7. ``plugin.api_version`` 必须能被**当前宿主 SDK 版本**满足（仅严格模式）——
   否则插件能过 CI、进索引，用户装完在激活时才失败；
8. ``--base-ref``（可选）：``plugin.json`` 或 ``*.py`` 改了却没 bump
   ``version`` → 失败。索引里的版本号必须诚实，否则升级检测与回滚都会失效；
9. ``--core-schema``（可选）：本仓 ``schema/`` 副本必须与主仓一致 ——
   副本漂移会让 PR 全绿但装不上。

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

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PLUGINS_DIRNAME = "plugins"
MANIFEST_FILENAME = "plugin.json"
REQUIRED_FILES = ("README.md", "LICENSE")

#: LICENSE 的最小字节数。只挡「写了个空文件或只写了一行 `MIT`」的情况，
#: 不做 SPDX 解析 —— 判断许可证文本是否"合法"不是本脚本该做的事。
MIN_LICENSE_BYTES = 20

#: 改动这些后缀的文件即视为「插件内容变更」，必须 bump version。
VERSION_BUMP_SUFFIXES = (".py",)

#: 维护者手工维护的下线名单（详见 build_index.py 同名常量处的说明）。
DEPRECATIONS_FILENAME = "deprecations.json"

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

    license_file = plugin_dir / "LICENSE"
    if license_file.is_file():
        size = license_file.stat().st_size
        if size < MIN_LICENSE_BYTES:
            reporter.fail(
                label,
                f"LICENSE 内容过短（{size} 字节）：请放完整许可证文本，"
                "而不是只写一个 SPDX 标识（如单独一行 MIT）",
            )

    # 注意：这里**不**扫描 ``__pycache__`` / ``*.pyc``。
    # 它们是本地跑过一次就会出现的运行时产物（`.gitignore` 已覆盖），
    # 若在此报错，贡献者每次本地自检后都会看到假失败。
    # "是否被误提交"交给 CI 用 ``git ls-files`` 判定（见 validate.yml）。

    if (plugin_dir / "__init__.py").is_file():
        reporter.fail(
            label,
            "插件目录内不应有顶层 __init__.py（会把目录变成包，与宿主按模块名导入的约定冲突）",
        )


def _check_api_version(section: dict, reporter: Reporter, label: str, *, mode: str) -> None:
    """``plugin.api_version`` 必须能被**当前宿主 SDK** 满足。

    为什么必须在收录时校验
    ----------------------
    ``api_version`` 表达的是"我需要哪个范围的宿主契约"。若当前宿主不满足，
    这个插件**装上去也用不了** —— 让它在索引里挂着，用户会依次经历
    安装 → 下载 → 解压 → 激活，最后才在激活阶段报错。代价远高于 PR 阶段报红。

    降级模式（无 SDK）下无法得知宿主版本，此处只做存在性检查；
    "兼容性未校验"已在降级 WARN 里显式声明，不假装通过。
    """
    spec = str(section.get("api_version") or "").strip()
    if not spec:
        reporter.fail(label, "plugin.api_version 为空（必填，如 '>=1.0,<2.0'）")
        return
    if mode != "sdk":
        return

    try:
        from sdk.version import SDK_API_VERSION, satisfies_version
    except ImportError:  # pragma: no cover - 与 mode 判定同源，不会单独发生
        return

    try:
        compatible = satisfies_version(SDK_API_VERSION, spec)
    except ValueError as exc:
        reporter.fail(label, f"api_version 格式非法 {spec!r}: {exc}")
        return

    if not compatible:
        reporter.fail(
            label,
            f"api_version {spec!r} 不被当前宿主 SDK {SDK_API_VERSION} 满足 —— "
            "用户装上也用不了；请调整区间，或等宿主升级后再提交",
        )


def _git(*args: str, cwd: Path) -> str | None:
    """执行 git 并返回 stdout；失败（无 git / 无该引用 / 非仓库）返回 ``None``。"""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _check_version_bump(
    plugin_dir: Path,
    section: dict,
    reporter: Reporter,
    *,
    repo_root: Path,
    base_ref: str,
) -> None:
    """改了插件内容却没 bump ``version`` → 失败。

    只把 ``plugin.json`` 与 ``*.py`` 算作"内容变更"；改 README / LICENSE
    不要求 bump —— 否则文档修订也会逼人升版本，大家就会随手写个假版本号，
    版本号反而更不可信。

    版本号不变而包内容（sha256）变化会同时破坏两件事：
    升级检测拿版本号判"有没有新版"，回滚拿版本号定位"上一个可用包"。
    """
    label = plugin_dir.name
    relative = f"plugins/{plugin_dir.name}"

    old_raw = _git("show", f"{base_ref}:{relative}/plugin.json", cwd=repo_root)
    if old_raw is None:
        return  # 基准里没有 → 新插件，无需 bump

    try:
        old_version = str((json.loads(old_raw).get("plugin") or {}).get("version") or "")
    except ValueError:
        return

    new_version = str(section.get("version") or "")
    if old_version != new_version:
        return  # 已 bump

    changed_raw = _git("diff", "--name-only", f"{base_ref}...HEAD", "--", relative, cwd=repo_root)
    if changed_raw is None:
        return

    changed = [line.strip() for line in changed_raw.splitlines() if line.strip()]
    content_changed = [
        path
        for path in changed
        if path.endswith(VERSION_BUMP_SUFFIXES) or path.endswith(MANIFEST_FILENAME)
    ]
    if not content_changed:
        return

    shown = ", ".join(content_changed[:5])
    more = " 等" if len(content_changed) > 5 else ""
    reporter.fail(
        label,
        f"内容已改动但 version 仍是 {new_version}（改动: {shown}{more}）。"
        "请 bump version：版本号不变而 sha256 变化会让升级检测与回滚同时失效。",
    )


def _check_schema_sync(core_schema: Path) -> int:
    """本仓 ``schema/`` 副本必须与主仓一致，返回问题数。

    ``schema/`` 是**副本**而不是独立定义：主仓改了字段约束而本仓没同步时，
    插件会按旧规则通过校验、进索引，然后在用户机器上装不上。
    """
    local_path = Path("schema") / "plugin-manifest.schema.json"
    if not local_path.is_file():
        print(f"[FAIL] 缺少 {local_path}", file=sys.stderr)
        return 1

    if not core_schema.is_file():
        print(
            f"[WARN] 找不到主仓 schema（{core_schema}），跳过一致性检查。\n"
            "       主仓为私有仓库时需配置 secrets.EASYAI_CORE_TOKEN 才能核对。",
            file=sys.stderr,
        )
        return 0

    try:
        local = json.loads(local_path.read_text(encoding="utf-8"))
        upstream = json.loads(core_schema.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"[FAIL] schema 无法解析: {exc}", file=sys.stderr)
        return 1

    if local == upstream:
        print(f"[OK] schema 与主仓一致（{local_path}）")
        return 0

    print(
        "[FAIL] schema 与主仓不一致 —— 主仓改了契约但本仓副本没同步。\n"
        f"       本仓: {local_path}\n"
        f"       主仓: {core_schema}\n"
        "       修复: 用主仓文件覆盖本仓副本（schema/ 是副本，不要本地改规则）。",
        file=sys.stderr,
    )
    return 1


def _check_deprecations(plugin_ids: set[str]) -> int:
    """校验 ``deprecations.json`` 的一致性，返回问题数。

    "下线"是**注册表动作**，最容易出的错不是格式，而是**指向不存在的插件** ——
    用户看到"已废弃，请迁移到 X"，点过去却是 404，比不提示更糟。
    """
    path = Path(DEPRECATIONS_FILENAME)
    if not path.is_file():
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"[FAIL] {path} 不是合法 JSON: {exc}", file=sys.stderr)
        return 1

    entries = data.get("deprecations") if isinstance(data, dict) else None
    if entries is None:
        return 0
    if not isinstance(entries, dict):
        print(f"[FAIL] {path} 的 deprecations 必须是对象", file=sys.stderr)
        return 1

    problems = 0
    for key, info in entries.items():
        if key not in plugin_ids:
            print(f"[FAIL] {path}: 引用了未收录的插件 {key!r}", file=sys.stderr)
            problems += 1
        if not isinstance(info, dict):
            print(f"[FAIL] {path}[{key}] 必须是对象", file=sys.stderr)
            problems += 1
            continue

        replacement = str(info.get("replacement") or "").strip()
        if not replacement:
            print(f"[FAIL] {path}[{key}] 缺少 replacement", file=sys.stderr)
            problems += 1
        elif replacement == key:
            print(f"[FAIL] {path}[{key}] 不能自我替代", file=sys.stderr)
            problems += 1
        elif replacement not in plugin_ids:
            print(
                f"[FAIL] {path}[{key}] 的 replacement 未收录: {replacement!r}",
                file=sys.stderr,
            )
            problems += 1

    return problems


def validate_plugin(
    plugin_dir: Path,
    *,
    contract: type | None,
    required_methods: tuple[str, ...],
    validate_manifest_file,
    reporter: Reporter,
    mode: str = "sdk",
    repo_root: Path | None = None,
    base_ref: str | None = None,
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
    _check_api_version(section, reporter, label, mode=mode)

    if repo_root is not None and base_ref:
        _check_version_bump(
            plugin_dir, section, reporter, repo_root=repo_root, base_ref=base_ref
        )

    # 确保 payload 至少被解析过（避免"看起来通过但其实没读文件"）
    del manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 plugins/ 下的插件是否满足收录要求")
    parser.add_argument(
        "--base-ref",
        default=None,
        help="版本 bump 检查的基准引用（CI 传 origin/main）；不传则跳过该检查",
    )
    parser.add_argument(
        "--core-schema",
        default=None,
        help="主仓 schema 文件路径，用于检测本仓副本是否漂移；不传则跳过",
    )
    args = parser.parse_args(argv)

    validate_manifest_file, contract, required_methods, mode = _load_validation_backends()

    schema_problems = 0
    if args.core_schema:
        schema_problems = _check_schema_sync(Path(args.core_schema))

    repo_root = Path(".").resolve()

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
            mode=mode,
            repo_root=repo_root,
            base_ref=args.base_ref,
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

    deprecation_problems = _check_deprecations(set(seen))

    if not reporter.ok() or schema_problems or deprecation_problems:
        reporter.dump()
        if not reporter.ok():
            print(f"\n[FAIL] 共 {len(reporter.problems)} 个问题", file=sys.stderr)
        return 1

    print(f"[OK] {len(plugin_dirs)} 个插件全部通过校验（模式: {mode}）")
    for plugin_dir in plugin_dirs:
        print(f"     - {plugin_dir.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
