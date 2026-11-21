"""插件后端的**加载方式**测试：必须能在 Hermes 的 import 隔离下工作。

为什么单独一个文件：`plugin_api.py` 在 Hermes 里是被
`plugin_isolation.import_user_code()` 以 `spec_from_file_location(...)` 当**顶层模块**
执行的（没有父包）。所以「用 TestClient 直接 import 通过」证明不了线上可用：
* 相对导入 `from .wbl_core import …` 在那里会 ImportError；
* 裸 `import wbl_core` 会走宿主 sys.path，真存在同名第三方包时**静默加载错的那个**；
* 往 `sys.path` 里 append 插件目录能跑通，但会污染宿主进程。

这里复现真实的加载方式，并钉住三条不变量：
1. 加载成功、`router` 存在、路由数正确；
2. 内核子包来自**本插件目录**，不是别处的同名包；
3. **不修改 sys.path**、不注册裸 `wbl_core`（否则会与宿主/其它插件撞名）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from wb_learn_support import DASHBOARD, PLUGIN_API

PROBE = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    api_path = Path(sys.argv[1])
    marker = "wb_learn_volume_core"

    # 先放一个「诱饵」同名包在 sys.path 上：如果插件后端用裸 import wbl_core，
    # 它会拿到这个诱饵而不是自己的内核 —— 那种实现应当被判失败。
    import tempfile
    bait_dir = Path(tempfile.mkdtemp())
    (bait_dir / "wbl_core").mkdir()
    (bait_dir / "wbl_core" / "__init__.py").write_text("VOLUME_VERSION = 'BAIT'\\n", encoding="utf-8")
    sys.path.insert(0, str(bait_dir))

    before_path = list(sys.path)
    before_mods = set(sys.modules)

    sys.path.insert(0, sys.argv[2])            # 让 hermes_cli 可导入
    from hermes_cli.plugin_isolation import import_user_code

    mod = import_user_code(api_path, "hermes_dashboard_plugin_wb-learn")
    assert mod is not None, "import_user_code 返回 None"
    assert hasattr(mod, "router"), "后端没有 router"

    core = sys.modules.get(marker)
    assert core is not None, "内核包没有按文件位置注册成 " + marker
    core_dir = Path(core.__file__).parent
    assert core_dir.name == "wbl_core", core_dir
    assert core_dir.parent == api_path.parent, f"内核来自错误的位置：{core_dir.parent}"

    assert mod.VOLUME_VERSION == "2.0.0", mod.VOLUME_VERSION

    # 诱饵没有被采用
    wbl = sys.modules.get("wbl_core")
    if wbl is not None:
        assert "BAIT" != getattr(wbl, "VOLUME_VERSION", None), "后端用了 sys.path 上的同名诱饵包"

    # 不污染 sys.path（除了我们为 hermes_cli 自己加的那一条）
    added = [p for p in sys.path if p not in before_path]
    plugin_dir = str(api_path.parent)
    assert plugin_dir not in added, f"后端把插件目录塞进了 sys.path：{added}"

    health = mod.health()
    assert health["ok"] is True and health["plugin"] == "wb-learn"
    print("ROUTES", len(mod.router.routes))
    print("CORE", core_dir)
    print("OK")
    """
)


def _run_probe() -> subprocess.CompletedProcess:
    hermes_agent = Path_or_none = os.environ.get("HERMES_AGENT_HOME")
    if Path_or_none is None:
        local = os.environ.get("LOCALAPPDATA") or str(__import__("pathlib").Path.home() / "AppData" / "Local")
        hermes_agent = str(__import__("pathlib").Path(local) / "hermes" / "hermes-agent")
    return subprocess.run(
        [sys.executable, "-c", PROBE, str(PLUGIN_API), hermes_agent],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )


def test_backend_loads_under_hermes_import_isolation():
    proc = _run_probe()
    if proc.returncode != 0:
        if "No module named 'hermes_cli'" in (proc.stderr or ""):
            pytest.skip("未安装 Hermes Agent（hermes_cli 不可导入）")
        pytest.fail(f"插件后端在 Hermes 加载方式下失败：\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    assert "OK" in proc.stdout, proc.stdout
    routes = [ln for ln in proc.stdout.splitlines() if ln.startswith("ROUTES")]
    assert routes and int(routes[0].split()[1]) >= 15, proc.stdout


def test_backend_has_no_relative_import_statement():
    """相对导入在顶层模块里必然失败；扫源码而不是等它运行时炸。"""
    src = PLUGIN_API.read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if ln.strip().startswith(("from .", "from ..", "import ."))]
    assert bad == [], f"plugin_api.py 含相对导入：{bad}"


def test_backend_does_not_bare_import_wbl_core():
    """裸 import 会走宿主 sys.path（可能拿到同名第三方包）。"""
    src = PLUGIN_API.read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in src.splitlines()
                 if ln.strip().startswith(("import wbl_core", "from wbl_core"))]
    assert offenders == [], f"plugin_api.py 用裸包名导入内核：{offenders}"


def test_backend_does_not_mutate_sys_path():
    src = PLUGIN_API.read_text(encoding="utf-8")
    assert "sys.path.append" not in src and "sys.path.insert" not in src, \
        "plugin_api.py 不应修改宿主 sys.path"


def test_core_package_imports_via_relative_imports_only():
    """内核内部必须用相对导入，才能在两种外层模块名下都成立。"""
    for path in (DASHBOARD / "wbl_core").glob("*.py"):
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8")
        assert "import wbl_core" not in src, f"{path.name} 用裸包名导入"
    init = (DASHBOARD / "wbl_core" / "__init__.py").read_text(encoding="utf-8")
    assert "from . import" in init
