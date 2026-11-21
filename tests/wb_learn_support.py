"""wb-learn 2.0 测试支撑：scratch 伪课程 / 伪自学 / 伪 vault，不碰真实用户资料。

铁律（本文件的核心作用）：
* 每个用例用 ``tmp_path`` 造自己的课程树、自学树、vault 与 plugin-data，
  通过 ``WB_LEARN_*`` 环境变量把内核指过去 —— 真实的 ``E:/Libraries/Courses``
  与 ``E:/Study`` 在这套用例里**完全不被读写**。
* 需要「真实小资料」的用例（真实解析能力）单独标记 ``real_data``，只读不写。

用法：``scratch_env(tmp_path)`` 返回 ``env`` 上下文管理器，里面的 ``.api()``
给出把环境变量设好之后的 ``plugin_api`` 模块（每次重新加载，避免模块级常量串味）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_dashboard() -> Path:
    """定位 dashboard 目录，兼容两种布局：

    * 原工程：``<repo>/plugins/wb-learn/dashboard``
    * 公开导出的独立仓库：``<repo>/wb-learn/dashboard``

    也可以用 ``WB_LEARN_DASHBOARD`` 显式指定，避免把某一种布局写死在测试里。
    """
    override = os.environ.get("WB_LEARN_DASHBOARD")
    if override:
        path = Path(override)
        if not (path / "plugin_api.py").is_file():
            raise RuntimeError(f"WB_LEARN_DASHBOARD 指向的目录没有 plugin_api.py：{path}")
        return path
    for rel in (Path("plugins") / "wb-learn" / "dashboard", Path("wb-learn") / "dashboard"):
        candidate = REPO_ROOT / rel
        if (candidate / "plugin_api.py").is_file():
            return candidate
    raise RuntimeError(
        f"找不到 wb-learn/dashboard（在 {REPO_ROOT} 下试过 plugins/wb-learn/dashboard 与 wb-learn/dashboard）"
    )


DASHBOARD = _find_dashboard()
PLUGIN_API = DASHBOARD / "plugin_api.py"

if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

_ENV_KEYS = (
    "WB_LEARN_COURSES_ROOT",
    "WB_LEARN_VAULT_ROOT",
    "WB_LEARN_STUDY_ROOT",
    "WB_LEARN_DATA_DIR",
)


_MODULE_PREFIXES = ("wbl_core", "wb_learn_volume")


def _purge_core_modules() -> None:
    """清掉内核包的所有模块形状，让下次加载重新读环境变量。

    内核在两种命名下都可能存在（取决于谁加载它）：
    * ``wbl_core`` / ``wbl_core.paths`` —— 测试里直接 import 时；
    * ``wb_learn_volume_core`` / ``wb_learn_volume_core.paths`` + 命名空间包
      ``wb_learn_volume`` —— Hermes 按文件路径加载 plugin_api.py 时。

    只清 ``wbl_core*`` 会漏掉后者，结果 plugin_api 拿到**指向上一个 tmp 目录**的
    缓存包（症状：本用例造的对象一个都扫不到）。所以两族前缀都清。
    """
    for name in list(sys.modules):
        if name.startswith(_MODULE_PREFIXES):
            sys.modules.pop(name, None)


def _alias_core_for_tests() -> None:
    """让测试里的 ``from wbl_core import paths`` 拿到**同一份**模块对象。

    plugin_api.py 按文件位置把内核注册成 ``wb_learn_volume_core``（为的是在 Hermes 的
    顶层模块加载下不撞名）。但测试里习惯写 ``from wbl_core import paths as P`` 再
    ``pytest.raises(P.PathNotAllowed)`` —— 如果那是另一份模块，**异常类不是同一个对象**，
    ``pytest.raises`` 就抓不到，测试会以「DID NOT RAISE」的形式误报。

    所以这里把已加载的 ``wb_learn_volume_core`` 及其子模块以 ``wbl_core*`` 的别名
    登记进 sys.modules（指向同一对象，不重新执行）。插件代码本身不依赖这个别名。
    """
    pkg = sys.modules.get("wb_learn_volume_core")
    if pkg is None:
        return
    sys.modules.setdefault("wbl_core", pkg)
    for name, mod in list(sys.modules.items()):
        if name.startswith("wb_learn_volume_core."):
            sys.modules.setdefault("wbl_core" + name[len("wb_learn_volume_core"):], mod)


class Scratch:
    """一套隔离的目录 + 加载好的 plugin_api 模块。"""

    def __init__(self, root: Path):
        self.root = root
        self.courses = root / "Courses"
        self.vault = self.courses / "vault" / "01-Courses"
        self.study = root / "Study"
        self.data = root / "plugin-data"
        for p in (self.courses, self.vault, self.study, self.data):
            p.mkdir(parents=True, exist_ok=True)
        self._mod = None

    # ---- 建树
    def course(self, name: str, files: dict | None = None) -> Path:
        d = self.courses / name
        d.mkdir(parents=True, exist_ok=True)
        for rel, content in (files or {}).items():
            self.write(d / rel, content)
        return d

    def study_topic(self, name: str, files: dict | None = None) -> Path:
        d = self.study / name
        d.mkdir(parents=True, exist_ok=True)
        for rel, content in (files or {}).items():
            self.write(d / rel, content)
        return d

    def vault_course(self, name: str, files: dict | None = None) -> Path:
        d = self.vault / name
        d.mkdir(parents=True, exist_ok=True)
        for rel, content in (files or {}).items():
            self.write(d / rel, content)
        return d

    def write(self, path: Path, content) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path.write_text(str(content), encoding="utf-8")
        return path

    # ---- 环境 + 模块
    def env(self) -> dict:
        return {
            "WB_LEARN_COURSES_ROOT": str(self.courses),
            "WB_LEARN_VAULT_ROOT": str(self.vault),
            "WB_LEARN_STUDY_ROOT": str(self.study),
            "WB_LEARN_DATA_DIR": str(self.data),
        }

    def api(self):
        """加载 plugin_api（内核模块级常量读环境变量，所以必须每次重载）。"""
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self.env())
        _purge_core_modules()
        name = f"hermes_dashboard_plugin_wb-learn-scratch-{self.root.name}"
        spec = importlib.util.spec_from_file_location(name, PLUGIN_API)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _alias_core_for_tests()
        self._mod = module
        return module

    # ---- 常用断言数据
    def read_mapping(self) -> dict:
        path = self.data / "learn-mapping.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def read_links(self) -> dict:
        path = self.data / "links.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def scratch(tmp_path) -> Scratch:
    s = Scratch(tmp_path / "wb-learn-scratch")
    yield s
    # 收尾：把环境变量还原，避免污染同进程的其它用例
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    _purge_core_modules()


# ---------------------------------------------------------------- 固定的题库样本
SAMPLE_BANK = {
    "schema_version": 1,
    "name": "测试题库",
    "questions": [
        {
            "id": "q-1", "type": "single", "stem": "机器视觉最核心的关键词是什么？",
            "options": [{"key": "A", "text": "测量、判断、控制"},
                        {"key": "B", "text": "采集、显示、存储"},
                        {"key": "C", "text": "编码、压缩、传输"},
                        {"key": "D", "text": "建模、仿真、渲染"}],
            "answer": ["A"],
            "explanation": "课程定义：用机器代替人眼做测量和判断，并控制现场设备动作。",
            "source": {"path": "第1章.md", "locator": "第 1 节"},
        },
        {
            "id": "q-2", "type": "multiple", "stem": "下列哪些属于机器视觉系统环节？",
            "options": [{"key": "A", "text": "光源"}, {"key": "B", "text": "镜头"},
                        {"key": "C", "text": "相机"}, {"key": "D", "text": "打印机"}],
            "answer": ["A", "B", "C"],
            "source": {"path": "第1章.md", "locator": "第 2 节"},
        },
        {
            "id": "q-3", "type": "judge", "stem": "机器视觉等同于计算机视觉。",
            "answer": ["错误"],
            "explanation": "两者侧重不同：CV 偏质，MV 偏量。",
            "source": {"path": "第1章.md", "locator": "第 3 节"},
        },
        {
            "id": "q-4", "type": "blank", "stem": "机器视觉的三个动作是____、____、____。",
            "blank_answers": [["测量"], ["判断"], ["控制"]],
            "source": {"path": "第1章.md", "locator": "第 1 节"},
        },
        {
            "id": "q-5", "type": "short", "stem": "简述张正友标定法的基本思路。",
            "answer": ["用平面标定板在不同位姿下拍摄，利用单应性约束求解内参，再优化畸变系数。"],
            "source": {"path": "第2章.md", "locator": "第 4 节"},
        },
    ],
}

TEXT_BANK = """第1章
一、单选题
1. 机器视觉最核心的关键词是以下哪一组？
A. 分类、识别、跟踪
B. 测量、判断、控制
C. 检测、分割、标注
D. 采集、处理、显示
答案：B
解析：机器视觉=用机器代替人眼做"测量和判断"。

二、判断题
2. 机器视觉与计算机视觉完全等同。
答案：错误
解析：两者侧重不同。

三、填空题
3. 机器视觉的三个动作是____、____、____。
答案：测量；判断；控制

四、简答题
4. 简述相机标定的目的。
参考答案：求内参矩阵与畸变系数，建立像素坐标与世界坐标的映射。
"""


def make_symlink_or_skip(src: Path, dst: Path) -> None:
    """造一个「指向 src 的链接」，优先用不需要管理员权限的 Windows junction。

    为什么不用 ``dst.symlink_to()``：Windows 下造真符号链接要 SeCreateSymbolicLinkPrivilege
    （普通用户没有，WinError 1314），而 ``mklink /J`` 造的 **junction 不需要提权**（实测可用）。
    junction 同样是一条逃逸路径 —— 而且 ``Path.is_symlink()`` 对它返回 False，
    所以用它测「链接逃逸」反而更严格。两者都失败才 skip。
    """
    if dst.is_symlink() or dst.exists():
        return
    errors = []

    if src.is_dir():
        try:
            import subprocess
            proc = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(dst), str(src)],
                                  capture_output=True, text=True, errors="replace")
            if proc.returncode == 0 and dst.exists():
                return
            errors.append(f"mklink /J rc={proc.returncode} {(proc.stderr or proc.stdout).strip()[:120]}")
        except OSError as exc:
            errors.append(f"mklink /J: {exc}")

    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
        return
    except (OSError, NotImplementedError) as exc:
        errors.append(f"symlink_to: {exc}")

    pytest.skip("当前环境既不能造 junction 也不能造符号链接：" + "；".join(errors))
