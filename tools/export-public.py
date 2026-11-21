"""把学习模块导出成独立可安装的公开仓库（本地生成，**不发布**）。

只导出源码、tests、示例、docs、配置模板与许可说明；下面这些一律不导出：

* 真实课程/笔记/题库内容（它们在用户磁盘上的 ``E:/…``，本来也不在仓库里，这里显式排除）
* 任何凭据（``.env``、token、cookie）
* 本地映射（``plugin-data/wb-learn/*.json``）
* 个人绝对路径（导出后由 ``scrub.py`` 扫描并报告）
* ``__pycache__`` / ``.pytest_cache`` / 草稿 / 备份

    python tools/export-public.py                    # 干跑，只打印计划
    python tools/export-public.py --dest D:/PersonalProjects/hermes-learning-workbench --apply

幂等：目标已存在时只覆盖导出清单内的文件，不删除目标里其它东西（除非 --prune）。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "plugins" / "wb-learn" / "dashboard"
DEFAULT_DEST = Path("D:/PersonalProjects/hermes-learning-workbench")

# 目录树：源（相对 REPO）→ 目标（相对 dest）
TREE = [
    ("plugins/wb-learn/dashboard", "wb-learn/dashboard"),
    ("shared/wb-agent-chat", "shared/wb-agent-chat"),
    ("tools/sync-shared.py", "tools/sync-shared.py"),
    ("tools/wb-enable.py", "tools/wb-enable.py"),
    ("tools/export-public.py", "tools/export-public.py"),
]

TESTS = [
    "conftest.py",
    "wb_learn_support.py",
    "test_wb_learn_paths.py",
    "test_wb_learn_catalog.py",
    "test_wb_learn_quiz.py",
    "test_wb_learn_generate.py",
    "test_wb_learn_materials.py",
    "test_wb_learn_links.py",
    "test_wb_learn_grade_parity.py",
    "test_wb_learn_ui_contract.py",
    "test_wb_learn_plugin_loader.py",
    "test_wb_learn_ui.py",
    "test_wb_chat_core.mjs",
    "test_wb_chat_client.mjs",
]

# 导出仓库自己的顶层文件（源在原工程里可能不存在，存在则以此处清单为准）
EXTRA_TOP = [".gitignore", "LICENSE", "NOTICE", "README.md", "config.example.yaml"]

# 无论出现在哪，都不导出
EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", ".git", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "drafts", "quiz-library", ".scratch-data",
    ".scratch-test", ".scratch-backup",
}
EXCLUDE_FILES = [
    "*.pyc", "*.pyo", "*.bak", "*.tmp", ".env", ".env.*", "*.log",
    "learn-mapping.json", "links.json", "*.sqlite", "*.sqlite3",
    "project-roots.json", "auth.txt", "cookies.txt",
    ".DS_Store", "Thumbs.db",
]

# 导出后必须**不存在**的东西：真正会泄漏个人环境或凭据的（命中即失败）
FORBIDDEN_CONTENT = [
    (Path.home().name, "本机用户名"),
    (str(Path.home()), "本机用户目录"),
    (Path.home().as_posix(), "本机用户目录"),
    ("ghp_", "GitHub token"),
    ("github_pat_", "GitHub fine-grained token"),
    ("zotero_api_key", "Zotero API key"),
]

# 允许但需要说明的「可配置默认值」：代码/文档里的默认根目录，用户可用环境变量改掉。
# 它们不含用户名、不含真实资料内容，属于产品默认值而不是隐私泄漏 —— 只作提示，不判失败。
NOTED_DEFAULTS = [
    ("E:/Libraries/Courses", "课程根默认值（可用 WB_LEARN_COURSES_ROOT 覆盖）"),
    ("E:/Study", "自学根默认值（可用 WB_LEARN_STUDY_ROOT 覆盖）"),
    ("D:/ActiveProjects", "项目根默认值（可用环境变量覆盖）"),
]

# 扫描器自己要跳过：它本身就写着这些特征串
SCAN_SKIP_FILES = {"tools/export-public.py"}


def excluded(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    name = path.name
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_FILES)


def walk(src: Path):
    if src.is_file():
        if not excluded(src):
            yield src, src.name
        return
    for item in sorted(src.rglob("*")):
        if item.is_dir() or excluded(item.relative_to(src)):
            continue
        yield item, item.relative_to(src).as_posix()


def plan(dest: Path) -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for src_rel, dst_rel in TREE:
        src = REPO / src_rel
        if not src.exists():
            print(f"  ! 跳过（不存在）：{src_rel}")
            continue
        for item, rel in walk(src):
            out.append((item, dest / dst_rel / rel if src.is_dir() else dest / dst_rel))
    for name in TESTS:
        src = REPO / "tests" / name
        if src.is_file():
            out.append((src, dest / "tests" / name))
        else:
            print(f"  ! 跳过（不存在）：tests/{name}")
    # docs/ 已随 dashboard 目录整体导出（QUIZ_SCHEMA.md 不重复加）
    return out


def scan_forbidden(dest: Path) -> tuple[list[str], list[str]]:
    """导出后自检。返回 ``(致命, 提示)``。

    致命 = 泄漏个人环境/凭据（用户名、内网地址、token）→ 必须修掉才能发布。
    提示 = 代码/文档里的**可配置默认根目录**（产品默认值，用户可用环境变量覆盖）。
    """
    fatal: list[str] = []
    notes: dict[str, str] = {}
    for path in sorted(dest.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".ico", ".pyc", ".webp"}:
            continue
        rel = path.relative_to(dest).as_posix()
        if rel in SCAN_SKIP_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle, label in FORBIDDEN_CONTENT:
            if needle in text:
                fatal.append(f"{rel}: 含{label}（{needle}）")
        for needle, label in NOTED_DEFAULTS:
            if needle in text:
                notes[needle] = label
    return fatal, [f"{needle} — {label}" for needle, label in notes.items()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认干跑）")
    ap.add_argument("--prune", action="store_true", help="删除目标里不在导出清单内的文件")
    args = ap.parse_args()

    items = plan(args.dest)
    if not args.apply:
        print(f"[干跑] 将导出 {len(items)} 个文件 → {args.dest}")
        for src, dst in items[:40]:
            print(f"  {src.relative_to(REPO)}  →  {dst.relative_to(args.dest)}")
        if len(items) > 40:
            print(f"  … 其余 {len(items) - 40} 个")
        return 0

    args.dest.mkdir(parents=True, exist_ok=True)
    written = 0
    # 导出仓库自己的顶层文件（README/LICENSE/NOTICE/.gitignore/config.example）只存在于
    # 目标里，源工程没有对应文件；--prune 时必须保护它们，否则会把文档删掉。
    keep = {(args.dest / name).resolve() for name in EXTRA_TOP}
    for src, dst in items:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        keep.add(dst.resolve())
        written += 1

    if args.prune:
        for path in sorted(args.dest.rglob("*"), reverse=True):
            if path.is_file() and path.resolve() not in keep:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    print(f"已导出 {written} 个文件 → {args.dest}")

    fatal, notes = scan_forbidden(args.dest)
    if notes:
        print("\n提示：以下可配置默认值仍出现在仓库里（属产品默认，可用环境变量覆盖）：")
        for n in notes:
            print(f"  · {n}")
    if fatal:
        print("\n隐私扫描**失败**（这些内容不该出现在公开仓库）：")
        for f in fatal:
            print(f"  - {f}")
        return 1
    print("隐私扫描通过：无用户名 / 内网地址 / token / 私有仓库信息")
    return 0


if __name__ == "__main__":
    sys.exit(main())
