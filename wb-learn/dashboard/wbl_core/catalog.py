"""课程 / 自主学习对象发现 + Obsidian 笔记关联 + 本地映射持久化。

两类学习对象：
* **课程** ``course:<课程名>``  —— 材料在 ``E:/Libraries/Courses/<课程>``
* **自主学习** ``study:<主题名>`` —— 材料在 ``E:/Study/<主题>``

笔记关联规则（不允许模糊自动错配）：
1. 同名精确匹配 → 自动关联，``link.kind = "exact"``；
2. 其余情况一律 ``linked = False``，把候选列出来等用户在 UI 里**显式**选择，
   选择结果写入插件自己的 ``plugin-data/wb-learn/learn-mapping.json``（``kind="manual"``）；
3. 显式选择「无笔记」也持久化（``vault = null``），避免每次重问；
4. 映射里指向不存在的目录时如实报 ``stale``，不静默回退到同名匹配。

扫描只做一层：不递归全盘，不跟符号链接，跳过 ``vault``/隐藏目录/``quiz-library``。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

MAPPING_NAME = "learn-mapping.json"
COURSE_PREFIX = "course:"
STUDY_PREFIX = "study:"

_LOCK = threading.Lock()
_ID_SAFE_RE = re.compile(r"^[^\x00-\x1f]{1,120}$")


# --------------------------------------------------------------------------
# 映射文件（插件自己的数据目录，不写用户资料）
# --------------------------------------------------------------------------
def store_dir() -> Path:
    override = os.environ.get("WB_LEARN_DATA_DIR")
    if override:
        return Path(override)
    home = os.environ.get("HERMES_HOME")
    if not home:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        home = str(Path(local) / "hermes")
    return Path(home) / "plugin-data" / "wb-learn"


def mapping_file() -> Path:
    return store_dir() / MAPPING_NAME


def load_mapping() -> Dict[str, Any]:
    path = mapping_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_mapping(mapping: Dict[str, Any]) -> None:
    _atomic_write(mapping_file(), json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# 对象发现
# --------------------------------------------------------------------------
def _roots() -> Dict[str, Path]:
    return paths.default_roots()


def object_dir(object_id: str) -> Path:
    """对象 id → 材料根目录（不做存在性判断，调用方决定）。

    名称段必须过 ``safe_child_name``：否则 ``course:C:/Windows`` 这种绝对路径会被
    pathlib 当成新的根（``root / "C:/Windows"`` == ``C:/Windows``），直接逃出扫描根。
    """
    kind_of(object_id)
    raw = object_id.split(":", 1)[1]
    name = paths.safe_child_name(raw)
    if name != raw:
        raise paths.PathNotAllowed("对象名称不允许规范化变形")
    root = _roots()["courses"] if object_id.startswith(COURSE_PREFIX) else _roots()["study"]
    candidate = root / name
    # 兜底：拼出来的路径必须仍在根内（安全网，不依赖 pathlib 的语义）
    if paths.safe_under(root, candidate) is None:
        return candidate          # 不存在时由调用方按「缺失」处理，但绝不静默放到根外
    return candidate


def kind_of(object_id: str) -> str:
    if object_id.startswith(COURSE_PREFIX):
        return "course"
    if object_id.startswith(STUDY_PREFIX):
        return "study"
    raise paths.PathNotAllowed("未知对象类型（只接受 course: / study: 前缀）")


def name_of(object_id: str) -> str:
    kind_of(object_id)
    return object_id.split(":", 1)[1]


def _short_desc(directory: Path) -> str:
    """读 README/NOTES/MISSION 的首个非空行当简介；读不到就是空串（不编）。"""
    for fname in ("README.md", "NOTES.md", "MISSION.md", "README.txt"):
        candidate = directory / fname
        if not candidate.is_file():
            continue
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in range(40):
                    line = fh.readline()
                    if not line:
                        break
                    text = line.strip().lstrip("#").strip()
                    if text and not text.startswith(("```", "---", ">")):
                        return text[:200]
        except OSError:
            continue
    return ""


def _counts(directory: Path) -> Dict[str, int]:
    """一层文件计数，供 UI 显示「有多少材料」，不做递归遍历。"""
    out = {"files": 0, "dirs": 0, "notes": 0}
    for child in paths.iter_children(directory):
        if child.is_dir():
            out["dirs"] += 1
        elif child.is_file():
            out["files"] += 1
            if child.suffix.lower() in paths.NOTE_SUFFIXES:
                out["notes"] += 1
    return out


def _vault_courses() -> List[str]:
    """vault/01-Courses 下真实存在的课程笔记目录名（一层）。"""
    vault = _roots()["vault"]
    if not vault.is_dir():
        return []
    return [p.name for p in paths.iter_children(vault) if p.is_dir()]


def _link_state(object_id: str, vault_names: List[str], mapping: Dict[str, Any]) -> Dict[str, Any]:
    """计算对象 → 笔记目录的关联状态。绝不模糊匹配。"""
    name = name_of(object_id)
    vault_root = _roots()["vault"]
    entry = mapping.get(object_id) if isinstance(mapping.get(object_id), dict) else None

    if entry is not None:
        target = entry.get("vault")
        if target is None:
            return {"linked": False, "kind": "none", "vault": None,
                    "vault_path": None, "stale": False,
                    "reason": "用户已显式选择「关联到无笔记」"}
        if isinstance(target, str) and target in vault_names:
            path = vault_root / target
            real = paths.safe_under(vault_root, path)
            if real is not None and real.is_dir():
                return {"linked": True, "kind": "manual", "vault": target,
                        "vault_path": str(real), "stale": False,
                        "reason": "用户在 UI 里显式指定的映射"}
        return {"linked": False, "kind": "manual", "vault": target if isinstance(target, str) else None,
                "vault_path": None, "stale": True, "candidates": vault_names,
                "reason": "映射里登记的笔记目录当前不存在（未静默改回同名匹配）"}

    if name in vault_names:
        path = vault_root / name
        real = paths.safe_under(vault_root, path)
        if real is not None and real.is_dir():
            return {"linked": True, "kind": "exact", "vault": name,
                    "vault_path": str(real), "stale": False,
                    "reason": "目录名精确同名，自动关联"}

    return {"linked": False, "kind": "unlinked", "vault": None, "vault_path": None,
            "stale": False, "candidates": vault_names,
            "reason": "没有同名笔记目录：请在 UI 显式选择（不做模糊自动匹配）"}


def _object_entry(object_id: str, vault_names: List[str], mapping: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = _roots()["courses"] if kind_of(object_id) == "course" else _roots()["study"]
    directory = object_dir(object_id)
    real = paths.safe_under(root, directory)
    if real is None or not real.is_dir():
        return None
    quiz_dir = real / "quiz-library"
    return {
        "id": object_id,
        "kind": kind_of(object_id),
        "name": name_of(object_id),
        "path": str(real),
        "description": _short_desc(real),
        "counts": _counts(real),
        "notes": _link_state(object_id, vault_names, mapping),
        "quiz_library": {
            "path": str(quiz_dir),
            "exists": quiz_dir.is_dir(),
        },
        # 资料根下是否存在 git 仓库（只读 discovery，给 GitHub 关联做候选）
        "git": _git_remote(real),
    }


def _git_remote(directory: Path) -> Optional[Dict[str, str]]:
    """只读发现 ``<dir>/.git/config`` 的 origin remote。不做任何 git 命令调用。"""
    cfg = directory / ".git" / "config"
    if not cfg.is_file():
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            continue
        if section is None or "remote \"origin\"" not in section:
            continue
        if stripped.lower().startswith("url") and "=" in stripped:
            url = stripped.split("=", 1)[1].strip()
            return {"remote_url": url}
    return None


def list_objects() -> Dict[str, Any]:
    """发现所有学习对象。根不存在就如实返回 exists=False，不伪造。"""
    roots = _roots()
    vault_names = _vault_courses()
    mapping = load_mapping()
    out: List[Dict[str, Any]] = []
    errors: List[str] = []

    for kind, prefix, key in (("course", COURSE_PREFIX, "courses"), ("study", STUDY_PREFIX, "study")):
        root = roots[key]
        if not root.is_dir():
            errors.append(f"{kind} 根目录不存在：{root}")
            continue
        for child in paths.iter_children(root):
            if not child.is_dir():
                continue
            if kind == "course" and child.name == "vault":
                continue                      # vault 自身不是课程
            if not _ID_SAFE_RE.match(child.name):
                continue
            entry = _object_entry(prefix + child.name, vault_names, mapping)
            if entry is not None:
                out.append(entry)

    out.sort(key=lambda o: (o["kind"], o["name"].lower()))
    return {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "roots": {
            "courses": {"path": str(roots["courses"]), "exists": roots["courses"].is_dir()},
            "study": {"path": str(roots["study"]), "exists": roots["study"].is_dir()},
            "vault": {"path": str(roots["vault"]), "exists": roots["vault"].is_dir()},
        },
        "vault_courses": vault_names,
        "count": len(out),
        "courses": [o for o in out if o["kind"] == "course"],
        "study": [o for o in out if o["kind"] == "study"],
        "objects": out,
        "mapping_file": str(mapping_file()),
        "errors": errors,
    }


def get_object(object_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(object_id, str) or ":" not in object_id:
        return None
    try:
        kind_of(object_id)
    except paths.PathNotAllowed:
        return None
    return _object_entry(object_id, _vault_courses(), load_mapping())


# --------------------------------------------------------------------------
# 映射写入（唯一允许的写操作之一）
# --------------------------------------------------------------------------
def set_note_mapping(object_id: str, vault_name: Optional[str]) -> Dict[str, Any]:
    """把对象 → 笔记目录（或 None）持久化。校验目录真实存在于 vault 下。"""
    obj = get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")

    if vault_name is not None:
        if not isinstance(vault_name, str) or not vault_name.strip():
            raise paths.PathNotAllowed("vault 名称非法")
        vault_name = vault_name.strip()
        if vault_name not in _vault_courses():
            raise paths.PathNotAllowed("该笔记目录不在 vault/01-Courses 中")
        real = paths.safe_under(_roots()["vault"], _roots()["vault"] / vault_name)
        if real is None or not real.is_dir():
            raise paths.PathNotAllowed("笔记目录不可解析（可能是链接或已移走）")

    with _LOCK:
        mapping = load_mapping()
        mapping[object_id] = {
            "vault": vault_name,
            "kind": "none" if vault_name is None else "manual",
            "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        save_mapping(mapping)

    fresh = get_object(object_id)
    return {"ok": True, "object": fresh, "mapping_file": str(mapping_file())}
