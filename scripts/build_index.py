#!/usr/bin/env python3
"""从 ``plugins/*/plugin.json`` 聚合生成 ``index.json``（**唯一的索引产出方式**）。

为什么必须自动生成
------------------
手写 ``index.json`` 迟早漂移：

* ``index`` 条目的 ``plugin_id`` 与 ``plugin.json`` 的 ``plugin.id`` 是**两套校验规则**
  （前者 ``^[A-Za-z0-9][A-Za-z0-9._-]*$``，不要求含点；后者要求含点）；
* ``sha256`` 与包内容更不可能靠手工保持同步 —— 插件改了一行，摘要就失效了。

所以本脚本是索引的**唯一真相源**，CI 与本地都只通过它产出。

可复现性（重要）
----------------
同一个插件目录必须打出**字节完全相同**的 zip，否则每次构建 sha256 都会变、
``index.json`` 一直抖动、``--check`` 永远失败。为此：

* 文件按路径排序后写入；
* 固定 ``date_time=(1980, 1, 1, 0, 0, 0)``（zip 格式允许的最早时间）；
* 固定压缩级别；
* 排除 ``__pycache__`` / ``.venv`` / ``*.pyc`` / ``.git`` 等非源码产物。

用法
----
::

    python scripts/build_index.py                # 生成 index.json（不打包）
    python scripts/build_index.py --pack         # 同时打 zip 到 dist/ 并写 sha256
    python scripts/build_index.py --pack --check # CI：校验磁盘文件是否与生成结果一致

退出码：``0`` 一致 / ``1`` 不一致或校验失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

INDEX_FILENAME = "index.json"
PLUGINS_DIRNAME = "plugins"
DIST_DIRNAME = "dist"
MANIFEST_FILENAME = "plugin.json"
RELEASE_TAG = "latest"

#: 打包时排除的目录名 / 后缀（非源码产物）。
EXCLUDED_DIRS = {"__pycache__", ".venv", "venv", ".git", ".pytest_cache", ".mypy_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}

#: 打包时按文本处理、需要把 CRLF 归一为 LF 的文件后缀。
#: **这是跨平台可复现的关键**：Windows 工作区是 CRLF、Linux CI 是 LF，
#: 若按原始字节打包，同一份源码在两边会算出不同的 sha256，
#: 导致"本地生成的索引与 CI 生成的不一致"。
TEXT_LIKE_SUFFIXES = {
    ".py", ".md", ".json", ".txt", ".yml", ".yaml",
    ".toml", ".cfg", ".ini", ".in", ".rst",
}
#: 无后缀但属于文本的文件名。
TEXT_LIKE_NAMES = {"LICENSE", "NOTICE", ".gitignore"}

#: zip 内时间戳固定值 —— 可复现构建的前提。
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

#: 条目必须能从 manifest 映射到的字段（缺失则该插件不合法，直接失败）。
#: ``api_version`` 也在必填之列：它是「我需要哪个范围的宿主契约」，
#: 缺了就只能靠内嵌 manifest 里那句字符串，市场列表无从提示兼容性。
REQUIRED_PLUGIN_FIELDS = ("id", "name", "version", "engine_kind", "api_version", "entry_point")


class BuildError(RuntimeError):
    """构建失败（数据不合法 / 环境缺参数）。"""


def _repo_slug(explicit: str | None) -> str:
    """解析 ``owner/repo``。

    优先级：显式参数 > GitHub Actions 的 ``GITHUB_REPOSITORY``。
    两者都没有时**直接失败** —— 猜一个默认值会让 index 里的下载地址永远 404，
    而那种错误只有在用户点安装时才会暴露。
    """
    if explicit:
        return explicit
    env = os.getenv("GITHUB_REPOSITORY", "").strip()
    if env:
        return env
    raise BuildError(
        "无法确定仓库地址：请传 --repo owner/name，"
        "或在 GitHub Actions 中运行（会自动提供 GITHUB_REPOSITORY）"
    )


def _iter_plugin_files(plugin_dir: Path) -> list[Path]:
    """列出需要打进包的文件（排序 + 过滤），保证可复现。"""
    files: list[Path] = []
    for path in plugin_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(plugin_dir).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(plugin_dir).as_posix())


def _read_for_pack(path: Path) -> bytes:
    """读取文件内容用于打包，**文本文件统一 CRLF → LF**。

    不做归一化时，同一份源码在 Windows（CRLF）与 Linux CI（LF）会打出
    不同字节的 zip，sha256 随之不同 —— 索引在任何一处生成，另一处就对不上。
    """
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_LIKE_SUFFIXES or path.name in TEXT_LIKE_NAMES:
        return data.replace(b"\r\n", b"\n")
    return data


def pack_plugin(plugin_dir: Path, dist_dir: Path, *, version: str) -> tuple[Path, str]:
    """把插件目录打成可复现的 zip，返回 ``(zip 路径, sha256 hex)``。

    zip 内**不含顶层目录**，文件平铺在根 —— 宿主解压到
    ``<插件根>/<plugin_id>/`` 即可，无需再剥一层。
    """
    dist_dir.mkdir(parents=True, exist_ok=True)
    archive = dist_dir / f"{plugin_dir.name}-{version}.zip"

    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
        for path in _iter_plugin_files(plugin_dir):
            arcname = path.relative_to(plugin_dir).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # ⚠️ ``create_system`` 默认值随平台变化：Windows 上是 0（FAT），
            # Unix 上是 3。它**写进 zip 头部**，不固定的话同一份源码在
            # Windows 与 Linux CI 上必然算出不同 sha256。
            info.create_system = 3
            # 固定权限位，避免不同文件系统上 umask 差异影响摘要
            info.external_attr = 0o644 << 16
            zf.writestr(info, _read_for_pack(path))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return archive, digest


def _load_manifest(plugin_dir: Path) -> dict:
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise BuildError(f"{plugin_dir.name}: 缺少 {MANIFEST_FILENAME}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BuildError(f"{plugin_dir.name}: {MANIFEST_FILENAME} 不是合法 JSON（{exc}）") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("plugin"), dict):
        raise BuildError(f"{plugin_dir.name}: {MANIFEST_FILENAME} 缺少顶层 plugin 段")

    section = payload["plugin"]
    missing = [name for name in REQUIRED_PLUGIN_FIELDS if not section.get(name)]
    if missing:
        raise BuildError(f"{plugin_dir.name}: plugin 段缺少必需字段 {missing}")
    return payload


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def build_entries(
    plugins_root: Path, *, repo_slug: str, pack: bool, dist_dir: Path
) -> list[dict]:
    """遍历插件目录，生成索引条目（按 plugin_id 稳定排序）。"""
    if not plugins_root.is_dir():
        raise BuildError(f"插件目录不存在: {plugins_root}")

    entries: list[dict] = []
    seen: dict[str, str] = {}

    for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        if plugin_dir.name.startswith(".") or plugin_dir.name.startswith("_"):
            continue

        manifest = _load_manifest(plugin_dir)
        section = manifest["plugin"]
        plugin_id = str(section["id"])

        # 目录名与 manifest id 必须一致：不一致时打包文件名与安装目标目录会错位，
        # 而这类错误在本地几乎不可能被发现（宿主按 id 找目录）。
        if plugin_dir.name != plugin_id:
            raise BuildError(
                f"目录名与 manifest id 不一致: 目录={plugin_dir.name!r} id={plugin_id!r}"
            )
        if plugin_id in seen:
            raise BuildError(f"重复的 plugin_id: {plugin_id}（{seen[plugin_id]} 与 {plugin_dir.name}）")
        seen[plugin_id] = plugin_dir.name

        version = str(section["version"])
        entry: dict = {
            "plugin_id": plugin_id,
            "name": str(section.get("name") or plugin_id),
            "version": version,
            # 宿主契约兼容区间（如 ">=1.0,<2.0"）。CI 会校验它能否被当前 SDK 满足；
            # 放进索引是为了让市场在**下载前**就能提示"此插件要求更高版本宿主"。
            "api_version": str(section.get("api_version") or ""),
            "engine_kind": str(section.get("engine_kind") or ""),
            "description": str(section.get("description") or ""),
            "isolation_mode": str((manifest.get("isolation") or {}).get("mode") or ""),
            "capabilities": list((manifest.get("contributes") or {}).get("capabilities") or []),
            "license": _optional_str(section.get("license")),
            "homepage": _optional_str(section.get("homepage")),
            "publisher": _optional_str((section.get("authors") or [None])[0]),
            "tags": list(((manifest.get("contributes") or {}).get("ui") or {}).get("tags") or []),
            # 内嵌完整 manifest：宿主据此判定可安装性（无需额外下载 plugin.json）
            "manifest": manifest,
        }

        if pack:
            archive, digest = pack_plugin(plugin_dir, dist_dir, version=version)
            entry["download_url"] = (
                f"https://github.com/{repo_slug}/releases/download/{RELEASE_TAG}/{archive.name}"
            )
            entry["sha256"] = digest
        else:
            entry["download_url"] = None
            entry["sha256"] = None

        entries.append(entry)

    entries.sort(key=lambda item: item["plugin_id"])
    return entries


def build_index(*, repo_slug: str, pack: bool, dist_dir: Path) -> dict:
    plugins_root = Path(PLUGINS_DIRNAME)
    entries = build_entries(plugins_root, repo_slug=repo_slug, pack=pack, dist_dir=dist_dir)
    return {
        "schema_version": 1,
        "name": "EasyAI Community Plugins",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plugins": entries,
    }


def _dump(index: dict) -> str:
    return json.dumps(index, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _comparable(index: dict) -> dict:
    """去掉随时间变化的 ``updated_at``，用于 ``--check`` 比较。"""
    clone = dict(index)
    clone.pop("updated_at", None)
    return clone


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 plugins/*/plugin.json 生成 index.json")
    parser.add_argument("--pack", action="store_true", help="同时打包 zip 到 dist/ 并写 sha256")
    parser.add_argument("--check", action="store_true", help="不写文件，仅校验是否与磁盘一致")
    parser.add_argument("--repo", default=None, help="owner/name（默认取 GITHUB_REPOSITORY）")
    parser.add_argument("--dist", default=DIST_DIRNAME, help="zip 输出目录")
    args = parser.parse_args(argv)

    try:
        repo_slug = _repo_slug(args.repo)
        index = build_index(repo_slug=repo_slug, pack=args.pack, dist_dir=Path(args.dist))
    except BuildError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    target = Path(INDEX_FILENAME)
    rendered = _dump(index)

    if args.check:
        if not target.is_file():
            print(f"[FAIL] 缺少 {INDEX_FILENAME}，请先运行 scripts/build_index.py --pack", file=sys.stderr)
            return 1
        current = json.loads(target.read_text(encoding="utf-8"))
        if _comparable(current) != _comparable(index):
            print(
                f"[FAIL] {INDEX_FILENAME} 与插件目录不一致，请重新运行 "
                "python scripts/build_index.py --pack 并提交",
                file=sys.stderr,
            )
            return 1
        print(f"[OK] {INDEX_FILENAME} 已是最新（{len(index['plugins'])} 个插件）")
        return 0

    # 内容无实质变化时**不写文件**：``updated_at`` 每次都会变，
    # 若照写不误，CI 会在每个 push 上产生一个只有时间戳差异的空提交。
    if target.is_file():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            current = None
        if current is not None and _comparable(current) == _comparable(index):
            print(f"[OK] {INDEX_FILENAME} 无实质变化，跳过写入（{len(index['plugins'])} 个插件）")
            return 0

    target.write_text(rendered, encoding="utf-8")
    print(f"[OK] 已写入 {INDEX_FILENAME}（{len(index['plugins'])} 个插件）")
    for entry in index["plugins"]:
        digest = entry.get("sha256")
        print(f"     - {entry['plugin_id']} v{entry['version']} sha256={(digest or '(未打包)')[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
