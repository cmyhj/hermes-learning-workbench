"""学习记录：点击次数、时间、按天统计。

存储于 ``$HERMES_HOME/plugin-data/wb-learn/learning-records.json``：

``{ "<object_id>": { "items": { "<item_id>": {count,last_at,first_at,type?} },
                      "entries": { "<entry_id>": {count,last_at,first_at} },
                      "daily": { "YYYY-MM-DD": N } } }``

## 语义

* **点击任何 item（论文/仓库/视频/网站）都计一次**，无论是否打开成功；
  失败由调用方在响应里带回 ``error``，计数不回滚。
* 计数**原子自增**：读-改-写全程持锁，再 ``os.replace`` 原子替换，并发不丢。
* ``daily`` 按对象级当日累计；``summarize`` 汇总总点击、按类型、最近 7/30 天、
  每个子条目与每个 item 的次数、最后访问时间。

按类型聚合需要 item 的类型：优先用记录里存的 ``type``，缺失时回查
``study.get_entries`` 映射（item 被删后历史仍可归类）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import catalog, paths, study

RECORDS_NAME = "learning-records.json"

_LOCK = threading.Lock()


class RecordStoreError(Exception):
    """learning-records.json 损坏（保留 .bak，可恢复）。"""


def records_file() -> Path:
    return catalog.store_dir() / RECORDS_NAME


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


def load_all() -> Dict[str, Any]:
    path = records_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        backup = path.with_suffix(".json.bak")
        try:
            os.replace(path, backup)
        except OSError:
            pass
        raise RecordStoreError(
            f"learning-records.json 损坏，已备份到 {backup}：{exc}") from exc
    return data if isinstance(data, dict) else {}


def save_all(data: Dict[str, Any]) -> None:
    _atomic_write(records_file(),
                  json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return _dt.date.today().isoformat()


def _check_object(object_id: str) -> str:
    if not isinstance(object_id, str) or not object_id.strip():
        raise paths.PathNotAllowed("缺少对象 id")
    object_id = object_id.strip()
    catalog.kind_of(object_id)
    # 学习记录只服务于自主学习子条目；课程对象没有子条目，不能在这里记账
    if not object_id.startswith(catalog.STUDY_PREFIX):
        raise paths.PathNotAllowed("学习记录只支持 study: 前缀的自主学习对象")
    return object_id


def _bucket(data: Dict[str, Any], object_id: str) -> Dict[str, Any]:
    bucket = data.setdefault(object_id, {"items": {}, "entries": {}, "daily": {}})
    bucket.setdefault("items", {})
    bucket.setdefault("entries", {})
    bucket.setdefault("daily", {})
    return bucket


def record_click(object_id: str, item_id: str, *,
                 item_type: Optional[str] = None,
                 entry_id: Optional[str] = None) -> Dict[str, Any]:
    """原子自增一次点击。返回该 item 的最新记录。"""
    _check_object(object_id)
    if not isinstance(item_id, str) or not item_id.strip():
        raise paths.PathNotAllowed("缺少 item_id")
    item_id = item_id.strip()
    now = _now()
    with _LOCK:
        data = load_all()
        bucket = _bucket(data, object_id)
        items = bucket["items"]
        rec = items.get(item_id)
        if not isinstance(rec, dict):
            rec = {"count": 0, "first_at": now}
            items[item_id] = rec
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["last_at"] = now
        rec.setdefault("first_at", now)
        if item_type:
            rec["type"] = item_type
        if entry_id:
            entries = bucket["entries"]
            erec = entries.get(entry_id)
            if not isinstance(erec, dict):
                erec = {"count": 0, "first_at": now}
                entries[entry_id] = erec
            erec["count"] = int(erec.get("count", 0)) + 1
            erec["last_at"] = now
            erec.setdefault("first_at", now)
        daily = bucket["daily"]
        today = _today()
        daily[today] = int(daily.get(today, 0)) + 1
        save_all(data)
    return rec


def load_object(object_id: str) -> Dict[str, Any]:
    _check_object(object_id)
    data = load_all()
    bucket = data.get(object_id)
    if not isinstance(bucket, dict):
        return {"items": {}, "entries": {}, "daily": {}}
    bucket.setdefault("items", {})
    bucket.setdefault("entries", {})
    bucket.setdefault("daily", {})
    return bucket


def _days_ago(n: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=n)).isoformat()


def summarize(object_id: str) -> Dict[str, Any]:
    """汇总对象级学习记录。

    返回：总点击、按类型、最近 7/30 天、每个子条目与每个 item 的次数、最后访问时间。
    """
    _check_object(object_id)
    bucket = load_object(object_id)
    items: Dict[str, Any] = bucket["items"]
    entries: Dict[str, Any] = bucket["entries"]
    daily: Dict[str, Any] = bucket["daily"]

    # 类型映射：优先记录里的 type，缺失回查 study 条目
    type_map: Dict[str, str] = {}
    for it_id, rec in items.items():
        t = rec.get("type")
        if t:
            type_map[it_id] = t
    try:
        for entry in study.get_entries(object_id):
            for it in entry.get("items") or []:
                iid = it.get("id")
                if iid and iid not in type_map:
                    type_map[iid] = it.get("type", "unknown")
    except study.StudyStoreError:
        pass  # 条目文件损坏不阻断记录汇总，用已存 type

    by_type: Dict[str, int] = {}
    for it_id, rec in items.items():
        t = type_map.get(it_id, "unknown")
        by_type[t] = by_type.get(t, 0) + int(rec.get("count", 0))

    def _window(days: int) -> int:
        since = _days_ago(days - 1)
        return sum(int(v) for k, v in daily.items() if k >= since)

    last_at = None
    for rec in items.values():
        la = rec.get("last_at")
        if la and (last_at is None or la > last_at):
            last_at = la

    return {
        "object_id": object_id,
        "total": sum(int(r.get("count", 0)) for r in items.values()),
        "by_type": by_type,
        "last_7d": _window(7),
        "last_30d": _window(30),
        "entries": {
            eid: {"count": int(r.get("count", 0)),
                  "last_at": r.get("last_at"), "first_at": r.get("first_at")}
            for eid, r in entries.items()
        },
        "items": {
            iid: {"count": int(r.get("count", 0)),
                  "type": type_map.get(iid, r.get("type", "unknown")),
                  "last_at": r.get("last_at"), "first_at": r.get("first_at")}
            for iid, r in items.items()
        },
        "daily": daily,
        "last_access": last_at,
    }
