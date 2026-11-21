"""路径安全：所有对外可见的绝对路径都必须先过这里。

三条硬规则（都是为了「不变成任意文件读取」）：
1. 只接受**已登记根**（``E:/Libraries/Courses``、``E:/Study``…）之下的路径；
2. 解析符号链接/junction 之后再判一次 ``is_relative_to``，防止链接逃逸到根外；
3. 拒绝任何含 ``..`` 的相对片段，拒绝非绝对路径。

客户端永远只传「对象 id」（如 ``course:机器视觉``、``study:RL教学``），
路径由服务端从登记表推导，绝不从请求里来。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterable, Optional

# Windows 的 reparse point 位（junction / symlink 都带它）；非 Windows 上该属性不存在
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 扫描时必须跳过的目录名：隐藏目录、Obsidian 库自身、依赖与缓存
SKIP_DIR_NAMES = {
    ".git", ".obsidian", ".trash", ".github", ".vscode", ".idea",
    "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache",
    ".ipynb_checkpoints", ".venv", "venv", "site-packages",
    "quiz-library",           # 题库目录不作为「材料」重复枚举
}

# 视为「笔记」的扩展名（Obsidian）
NOTE_SUFFIXES = {".md", ".markdown"}

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv", ".py",
                 ".c", ".h", ".cpp", ".hpp", ".ipynb", ".tex", ".log", ".toml", ".ini",
                 ".cfg", ".sh", ".ps1", ".bat", ".rst", ".xml", ".html", ".m"}

DOC_SUFFIXES = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".wav", ".m4a", ".avi"}


class PathNotAllowed(Exception):
    """请求越过已登记根，或路径形状非法。"""


def is_link(p: Path) -> bool:
    """符号链接、Windows junction、任何 reparse point 都算链接。

    为什么不能只看 ``is_symlink()``：Windows 上 ``mklink /J`` 造的 junction **不需要管理员权限**
    （实测可用），而 ``Path.is_symlink()`` 对它返回 False；``Path.is_junction()`` 要到
    Python 3.12 才有，Hermes 的 dashboard 可能跑在 3.11/3.14 两种解释器上。
    所以这里三种判据都查：is_symlink → is_junction（若有）→ lstat 的 REPARSE_POINT 属性。
    漏掉 junction 就等于漏掉一条不需要提权的逃逸路径。
    """
    try:
        if p.is_symlink():
            return True
    except OSError:
        return True
    try:
        is_junction = getattr(p, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    except OSError:
        return True
    try:
        st = os.lstat(p)
    except OSError:
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE_POINT)


def safe_under(root: Path, candidate: Path, *, allow_root: bool = False) -> Optional[Path]:
    """``candidate`` 是否真的落在 ``root`` 之内；是则返回 resolve 后的路径，否则 None。

    resolve() 会解开链接，所以「根内的链接指向根外」在这里被挡掉。
    """
    try:
        real_root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if real == real_root:
        return real if allow_root else None
    try:
        if not real.is_relative_to(real_root):
            return None
    except AttributeError:  # py<3.9 兜底
        try:
            real.relative_to(real_root)
        except ValueError:
            return None
    return real


def assert_under(root: Path, candidate: Path, *, allow_root: bool = False) -> Path:
    got = safe_under(root, candidate, allow_root=allow_root)
    if got is None:
        raise PathNotAllowed(f"路径不在允许的根内：{candidate}")
    return got


def rel_display(root: Path, target: Path) -> str:
    """返回相对根的 posix 路径；失败就返回文件名（不抛）。"""
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError, RuntimeError):
        return target.name


def safe_child_name(name: str) -> str:
    """单段文件名/目录名：不允许分隔符、``..``、盘符、控制字符。"""
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise PathNotAllowed("名称长度非法")
    if name in (".", ".."):
        raise PathNotAllowed("名称非法")
    for bad in ("/", "\\", ":", "*", "?", '"', "<", ">", "|", "\x00"):
        if bad in name:
            raise PathNotAllowed(f"名称含非法字符：{bad!r}")
    if name.startswith("."):
        raise PathNotAllowed("名称不能以点开头")
    if name.endswith((".", " ")):
        raise PathNotAllowed("Windows 名称不能以点或空格结尾")
    if any(ord(ch) < 32 for ch in name):
        raise PathNotAllowed("名称不能包含控制字符")
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}:
        raise PathNotAllowed("名称是 Windows 保留设备名")
    return name


def iter_children(directory: Path, *, skip_hidden: bool = True) -> Iterable[Path]:
    """只读列一层子项（不递归），跳过隐藏项、链接与 SKIP_DIR_NAMES。

    显式不递归：``E:/Libraries/Courses`` 下是课程目录，再往下是实验/资料树，
    全盘递归既慢又容易撞到 repo/node_modules。
    """
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    out = []
    for p in entries:
        if skip_hidden and p.name.startswith("."):
            continue
        if p.name in SKIP_DIR_NAMES:
            continue
        if is_link(p):
            continue          # 链接一律不跟：这是逃逸防护的第一道闸
        out.append(p)
    return out


def classify_suffix(path: Path) -> str:
    s = path.suffix.lower()
    if s in NOTE_SUFFIXES:
        return "note"
    if s in TEXT_SUFFIXES:
        return "text"
    if s in DOC_SUFFIXES:
        return "doc"
    if s in IMAGE_SUFFIXES:
        return "image"
    if s in MEDIA_SUFFIXES:
        return "media"
    return "other"


def default_roots() -> dict:
    """本机默认根；可用环境变量覆盖（测试用 scratch，绝不碰真实资料）。"""
    courses = Path(os.environ.get("WB_LEARN_COURSES_ROOT") or "E:/Libraries/Courses")
    vault = Path(os.environ.get("WB_LEARN_VAULT_ROOT")
                 or str(courses / "vault" / "01-Courses"))
    study = Path(os.environ.get("WB_LEARN_STUDY_ROOT") or "E:/Study")
    return {"courses": courses, "vault": vault, "study": study}
