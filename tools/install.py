#!/usr/bin/env python
"""把学习模块安装到 Hermes 的 profile（复制 + 校验 + 提示启用方式）。

它做三件事，**不碰你的 config.yaml**：
1. 复制 ``wb-learn/`` 到 ``$HERMES_HOME/plugins/wb-learn/``（默认不覆盖已有文件，除非 --force）；
2. 把 ``shared/wb-agent-chat/*`` 同步进 ``dist/vendor/``（共享组件的 vendored 副本）；
3. 自检：manifest 可解析、entry/css/api 文件存在、后端能按 Hermes 的方式 import、
   ``plugins.enabled`` 是否已包含 wb-learn（只报告，不修改）。

为什么不由脚本直接改 config.yaml：``hermes config set`` 会整体替换数组，脚本替换用户的
配置是最容易丢条目的做法。这里只打印需要执行的命令，让人自己确认。

    python tools/install.py                    # 干跑：打印计划与自检结果
    python tools/install.py --apply            # 真的复制
    python tools/install.py --apply --force    # 覆盖已存在的文件
    python tools/install.py --apply --check-backend   # 额外按 Hermes 加载方式 import 一次
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO / "wb-learn"
if not PLUGIN_SRC.is_dir():                      # 兼容原工程布局
    PLUGIN_SRC = REPO / "plugins" / "wb-learn"
DASHBOARD_SRC = PLUGIN_SRC / "dashboard"
SHARED_SRC = REPO / "shared" / "wb-agent-chat"

SKIP_NAMES = {"__pycache__", ".pytest_cache", "drafts", "quiz-library",
              ".scratch-data", ".scratch-test", ".scratch-backup"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".bak", ".tmp", ".log"}


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "hermes"


def iter_files(src: Path):
    for item in sorted(src.rglob("*")):
        if item.is_dir() or item.suffix in SKIP_SUFFIXES:
            continue
        if any(part in SKIP_NAMES for part in item.relative_to(src).parts):
            continue
        yield item


def self_check(target: Path) -> list[str]:
    """安装后的静态自检。返回问题清单（空 = 通过）。"""
    problems: list[str] = []
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return [f"manifest.json 缺失：{manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json 不是合法 JSON：{exc}"]

    for key in ("entry", "css", "api"):
        rel = manifest.get(key)
        if rel and not (target / rel).is_file():
            problems.append(f"manifest.{key} 指向的文件不存在：{target / rel}")
    if not (target / "wbl_core" / "__init__.py").is_file():
        problems.append("wbl_core/ 内核包缺失（后端会 ImportError）")
    for name in ("wb-agent-chat.js", "wb-agent-chat.css"):
        if not (target / "dist" / "vendor" / name).is_file():
            problems.append(f"dist/vendor/{name} 缺失（对话面板会加载失败）")
    return problems


def backend_probe(target: Path) -> tuple[bool, str]:
    """按 Hermes 的真实加载方式 import 一次后端（顶层模块，无父包）。"""
    probe = r"""
import sys
from pathlib import Path
api = Path(sys.argv[1]); sys.path.insert(0, sys.argv[2])
from hermes_cli.plugin_isolation import import_user_code
mod = import_user_code(api, "hermes_dashboard_plugin_wb-learn")
assert mod is not None and hasattr(mod, "router")
h = mod.health()
assert h["ok"] is True
print(f"routes={len(mod.router.routes)} objects={h['objects']} version={h['version']}")
"""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    hermes_agent = os.environ.get("HERMES_AGENT_HOME") or str(Path(local) / "hermes" / "hermes-agent")
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(target / "plugin_api.py"), hermes_agent],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or "").strip()
    return False, ((proc.stdout or "") + (proc.stderr or "")).strip()[-800:]


def enabled_state() -> str:
    """只读地查 plugins.enabled 是否含 wb-learn（不修改配置）。"""
    try:
        from hermes_cli.plugins_cmd import _get_enabled_set  # noqa: PLC0415
        return "enabled" if "wb-learn" in _get_enabled_set() else "missing"
    except Exception:
        pass
    cfg = hermes_home() / "config.yaml"
    if not cfg.is_file():
        return "unknown"
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    return "enabled" if "wb-learn" in text else "missing"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的复制（默认干跑）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    ap.add_argument("--check-backend", action="store_true", help="按 Hermes 的方式 import 后端")
    ap.add_argument("--target", type=Path, default=None, help="覆盖目标目录（测试用）")
    args = ap.parse_args()

    home = hermes_home()
    # 注意目录层级：Hermes 发现的是 $HERMES_HOME/plugins/<name>/dashboard/manifest.json，
    # 所以 dashboard 内容必须落在 plugins/wb-learn/dashboard/ 下 —— 直接铺到
    # plugins/wb-learn/ 会被扫描器忽略（插件「装了但看不见」）。
    plugin_root = args.target or (home / "plugins" / "wb-learn")
    target = plugin_root / "dashboard"
    if not DASHBOARD_SRC.is_dir():
        print(f"找不到插件源码：{DASHBOARD_SRC}", file=sys.stderr)
        return 2

    files = list(iter_files(DASHBOARD_SRC))
    print(f"源：{DASHBOARD_SRC}")
    print(f"目标：{target}")
    print(f"文件数：{len(files)}"
          + ("（含 shared 同步）" if SHARED_SRC.is_dir() else "（shared/ 不存在，跳过 vendored 同步）"))

    if not args.apply:
        existing = [f for f in files if (target / f.relative_to(DASHBOARD_SRC)).exists()]
        print(f"将写入 {len(files)} 个文件；目标已存在 {len(existing)} 个"
              + ("" if args.force else "（不加 --force 则跳过）"))
        print("\n[干跑] 未写盘。加 --apply 执行。")
        return 0

    written = skipped = 0
    for src in files:
        dst = target / src.relative_to(DASHBOARD_SRC)
        if dst.exists() and not args.force:
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written += 1

    synced = 0
    if SHARED_SRC.is_dir():
        vendor = target / "dist" / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        for src in sorted(SHARED_SRC.glob("*")):
            if src.is_file() and src.suffix in (".js", ".css"):
                shutil.copy2(src, vendor / src.name)
                synced += 1

    print(f"已写入 {written} 个文件，跳过 {skipped} 个"
          + (f"，同步 vendored {synced} 个" if synced else ""))

    for d in (target / "__pycache__", target / "wbl_core" / "__pycache__"):
        shutil.rmtree(d, ignore_errors=True)

    problems = self_check(target)
    if problems:
        print("\n自检失败：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("自检通过：manifest / entry / css / api / 内核包 / vendored 组件齐全")

    state = enabled_state()
    print(f"plugins.enabled 状态：{state}")
    if state != "enabled":
        print("  启用方式（不要手改 YAML，先读后写）：")
        print("    hermes config get plugins.enabled")
        print("    hermes config set plugins.enabled '[\"<已有条目>\",\"wb-learn\"]'")
        print("  或：python tools/wb-enable.py --apply")

    if args.check_backend:
        ok, detail = backend_probe(target)
        print(("后端 import 通过：" if ok else "后端 import 失败：") + detail)
        if not ok:
            return 1

    print("\n改过 plugin_api.py 或 wbl_core/* 后需要重启 dashboard 才生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
