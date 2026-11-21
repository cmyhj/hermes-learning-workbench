"""quiz-library 读写：读取、草稿 staging、校验后原子保存。

## 目录约定（固定相对路径，不迁移用户已有题库）
```
<对象目录>/quiz-library/*.json      # 标准格式（schema v1）
<对象目录>/quiz-library/*.txt       # 既有机器视觉题库格式（只读解析，不重写）
```
* 只有这个目录允许写入；写入前 ``safe_under`` + ``safe_child_name`` 双重校验，
  名字里带分隔符/``..``/盘符一律拒绝，防路径穿越。
* **覆盖必须显式确认**：目标已存在且 ``overwrite`` 不为真 → 409，绝不静默替换。
* 保存是原子的：写同目录临时文件 → ``os.replace``，中途失败不会留下半个题库。

## 草稿（staging）
agent 生成的结构化 JSON 先落到插件数据目录 ``plugin-data/wb-learn/drafts/<id>.json``，
用户在 UI 里看到预览与校验结果、确认后才复制进 ``quiz-library``。
草稿目录不参与扫描，所以「生成了但没确认」不会出现在题库列表里 —— 不会假装已生成。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import catalog, paths
from . import quiz_schema as schema

QUIZ_DIR_NAME = "quiz-library"
MAX_BANK_BYTES = 8 * 1024 * 1024
MAX_BANKS = 200

_LOCK = threading.Lock()
_DRAFT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


# --------------------------------------------------------------------------
# 目录
# --------------------------------------------------------------------------
def quiz_dir(object_id: str) -> Path:
    """对象 → quiz-library 绝对路径（调用方负责存在性判断）。"""
    return catalog.object_dir(object_id) / QUIZ_DIR_NAME


def ensure_quiz_dir(object_id: str) -> Path:
    """创建 quiz-library（仅此目录可写）。越界直接抛。"""
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    target = quiz_dir(object_id)
    root = catalog.object_dir(object_id)
    if paths.safe_under(root, target) is None and not target.exists():
        # 尚未创建：先确认父目录解析后仍在根内
        parent = paths.safe_under(root, root, allow_root=True)
        if parent is None:
            raise paths.PathNotAllowed("对象目录不可解析")
    target.mkdir(parents=False, exist_ok=True)
    real = paths.safe_under(root, target)
    if real is None:
        raise paths.PathNotAllowed("quiz-library 解析后不在对象目录内")
    return real


def _drafts_dir() -> Path:
    return catalog.store_dir() / "drafts"


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_BANK_BYTES:
        raise ValueError(f"题库文件超过 {MAX_BANK_BYTES // 1024 // 1024}MB 上限")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _bank_summary(path: Path, object_id: str) -> Dict[str, Any]:
    """轻量摘要：只解析一次，失败时把错误如实带出，不隐藏坏文件。"""
    rel = paths.rel_display(catalog.object_dir(object_id), path)
    entry: Dict[str, Any] = {
        "id": path.stem,
        "file": path.name,
        "rel_path": rel,
        "path": str(path),
        "format": "json" if path.suffix.lower() == ".json" else "text",
        "bytes": path.stat().st_size,
        "mtime": _dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
    }
    try:
        if entry["format"] == "json":
            data = _read_json(path)
            bank = schema.validate_bank(data)
            entry.update({
                "name": bank["name"] or path.stem,
                "total": bank["total"],
                "counts": bank["counts"],
                "ok": bank["ok"],
                "issues": bank["issues"][:20],
                "generated": bank.get("generated"),
            })
            if not bank["ok"]:
                entry["parse_error"] = "题库校验未通过（详见 issues）"
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            parsed = schema.parse_text_bank(text, rel)
            counts = {t: 0 for t in schema.TYPES}
            for q in parsed["questions"]:
                counts[q["type"]] = counts.get(q["type"], 0) + 1
            entry.update({
                "name": path.stem,
                "total": len(parsed["questions"]),
                "counts": counts,
                "ok": not parsed["issues"] and bool(parsed["questions"]),
                "issues": parsed["issues"][:20],
                "sections": list(dict.fromkeys(parsed["sections"])),
                "read_only": True,   # 既有 txt 题库：只读解析，不提供编辑/保存
            })
            if not parsed["questions"]:
                entry["parse_error"] = "文件存在但没有解析出题目（格式可能变了）"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        entry.update({"ok": False, "total": 0, "counts": {}, "issues": [],
                      "parse_error": f"{type(exc).__name__}: {exc}"})
    return entry


def list_banks(object_id: str) -> Dict[str, Any]:
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    directory = quiz_dir(object_id)
    out: List[Dict[str, Any]] = []
    errors: List[str] = []
    if directory.is_dir():
        real_dir = paths.safe_under(catalog.object_dir(object_id), directory)
        if real_dir is None:
            errors.append("quiz-library 解析后不在对象目录内（可能是链接），已拒绝扫描")
        else:
            files = [p for p in real_dir.iterdir()
                     if p.suffix.lower() in (".json", ".txt") and p.is_file()]
            files.sort(key=lambda p: p.name.lower())
            for path in files[:MAX_BANKS]:
                try:
                    out.append(_bank_summary(path, object_id))
                except OSError as exc:
                    errors.append(f"{path.name}: {exc}")
            if len(files) > MAX_BANKS:
                errors.append(f"题库文件超过 {MAX_BANKS} 个，只列出前 {MAX_BANKS} 个")
    return {
        "object": {k: obj[k] for k in ("id", "kind", "name", "path")},
        "quiz_library": {"path": str(directory), "exists": directory.is_dir()},
        "count": len(out),
        "banks": out,
        "errors": errors,
    }


def _locate_bank(object_id: str, bank_id: str) -> Path:
    """bank id → 真实文件路径。只认 list_banks 里的 id，不接受文件名/路径。"""
    if not isinstance(bank_id, str) or not bank_id.strip():
        raise paths.PathNotAllowed("题库 id 非法")
    bank_id = bank_id.strip()
    listed = list_banks(object_id)
    for bank in listed["banks"]:
        if bank["id"] == bank_id:
            root = catalog.object_dir(object_id)
            real = paths.safe_under(root, Path(bank["path"]))
            if real is None or not real.is_file():
                raise paths.PathNotAllowed("题库文件不可解析")
            return real
    raise FileNotFoundError(f"题库不存在：{bank_id}")


def load_bank(object_id: str, bank_id: str, *, limit: int = 300,
              qtype: Optional[str] = None) -> Dict[str, Any]:
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    if qtype is not None and qtype not in schema.TYPES:
        raise ValueError(f"qtype 只能是 {'/'.join(schema.TYPES)}")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(limit, 1000))

    path = _locate_bank(object_id, bank_id)
    rel = paths.rel_display(catalog.object_dir(object_id), path)
    issues: List[str] = []
    if path.suffix.lower() == ".json":
        bank = schema.validate_bank(_read_json(path))
        questions = bank["questions"]
        meta = {k: bank[k] for k in ("name", "object_id", "generated", "counts", "ok", "issues", "warnings")}
        issues = bank["issues"]
        sections: List[str] = []
    else:
        parsed = schema.parse_text_bank(path.read_text(encoding="utf-8", errors="replace"), rel)
        questions = parsed["questions"]
        counts = {t: sum(1 for q in questions if q["type"] == t) for t in schema.TYPES}
        meta = {"name": path.stem, "object_id": object_id, "generated": None,
                "counts": counts, "ok": not parsed["issues"], "issues": parsed["issues"],
                "warnings": []}
        issues = parsed["issues"]
        sections = list(dict.fromkeys(parsed["sections"]))

    filtered = [q for q in questions if qtype is None or q["type"] == qtype]
    return {
        "object": {k: obj[k] for k in ("id", "kind", "name", "path")},
        "bank": {"id": path.stem, "file": path.name, "rel_path": rel, "path": str(path),
                 "format": "json" if path.suffix.lower() == ".json" else "text",
                 "read_only": path.suffix.lower() != ".json", **meta},
        "sections": sections,
        "total": len(questions),
        "returned": len(filtered[:limit]),
        "questions": filtered[:limit],
        "issues": issues,
        # 答案随题返回：这是自测题库，不是监考；判分复用 quiz_schema.grade
        "grading": {
            "kinds": list(schema.TYPES),
            "auto": ["single", "multiple", "judge", "blank"],
            "manual": ["short"],
            "note": "简答题不自动判分，只展示参考答案；不做关键词假判。",
        },
    }


# --------------------------------------------------------------------------
# 草稿 staging
# --------------------------------------------------------------------------
def _draft_path(draft_id: str) -> Path:
    if not isinstance(draft_id, str) or not _DRAFT_RE.match(draft_id):
        raise paths.PathNotAllowed("草稿 id 形状非法")
    return _drafts_dir() / f"{draft_id}.json"


def stage_draft(object_id: str, payload: Any, *, title: Optional[str] = None) -> Dict[str, Any]:
    """把（agent 产出的）题库 JSON 存成草稿并返回校验结果。**不写 quiz-library**。"""
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")

    bank = schema.validate_bank(payload)
    digest = hashlib.sha256(
        json.dumps([bank["questions"], object_id], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    draft_id = f"{bank['schema_version']}-{digest}"
    name = (title or bank.get("name") or "").strip()

    suggested = _safe_stem(name) if name else f"draft-{digest}"
    record = {
        "draft_id": draft_id,
        "object_id": object_id,
        "title": name or suggested,
        "suggested_file": f"{suggested}.json",
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "schema_version": bank["schema_version"],
        "counts": bank["counts"],
        "total": bank["total"],
        "ok": bank["ok"],
        "issues": bank["issues"],
        "warnings": bank["warnings"],
        "preview": bank["questions"][:20],
        "questions": bank["questions"],
    }
    target = _draft_path(draft_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return {k: v for k, v in record.items() if k != "questions"} | {"path": str(target)}


def _safe_stem(name: str) -> str:
    """把标题变成安全文件名主干：去分隔符/控制字符，保留中文。"""
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", (name or "").strip())
    stem = re.sub(r"\s+", "-", stem).strip("-.")
    return stem[:60] or "quiz"


def get_draft(draft_id: str) -> Dict[str, Any]:
    path = _draft_path(draft_id)
    if not path.is_file():
        raise FileNotFoundError("草稿不存在")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
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


def commit_draft(object_id: str, draft_id: str, *, filename: Optional[str] = None,
                 overwrite: bool = False) -> Dict[str, Any]:
    """用户确认后：草稿 → ``<对象>/quiz-library/<name>.json``（原子写）。

    拒绝：未通过校验的草稿、越界文件名、已存在且未声明覆盖的目标。
    """
    record = get_draft(draft_id)
    if record.get("object_id") != object_id:
        raise paths.PathNotAllowed("草稿不属于该学习对象")
    if not record.get("ok"):
        raise ValueError("草稿未通过校验，不能保存：" + "；".join(record.get("issues") or [])[:400])

    name = (filename or record.get("suggested_file") or f"{draft_id}.json").strip()
    if not name.lower().endswith(".json"):
        name += ".json"
    stem = name[:-5]
    safe_stem = paths.safe_child_name(stem)
    final_name = f"{safe_stem}.json"

    directory = ensure_quiz_dir(object_id)
    target = directory / final_name
    real = paths.safe_under(directory, target) if target.exists() else None
    if target.exists() and real is None:
        raise paths.PathNotAllowed("目标路径解析后不在 quiz-library 内")
    if target.exists() and not overwrite:
        raise FileExistsError(f"已存在同名题库：{final_name}（覆盖需显式确认）")

    with _LOCK:
        payload = {
            "schema_version": schema.SCHEMA_VERSION,
            "name": record.get("title") or safe_stem,
            "object_id": object_id,
            "generated": {"by": (record.get("generated") or {}).get("by") or "hermes-agent",
                          "at": (record.get("generated") or {}).get("at") or record.get("created_at"),
                          "model": (record.get("generated") or {}).get("model")},
            "questions": record["questions"],
        }
        _atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        saved = target.stat()

    return {
        "ok": True,
        "object_id": object_id,
        "file": final_name,
        "path": str(target),
        "rel_path": paths.rel_display(catalog.object_dir(object_id), target),
        "total": len(payload["questions"]),
        "bytes": saved.st_size,
        "overwrote": bool(overwrite),
    }
