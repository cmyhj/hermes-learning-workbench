"""自主学习子条目：数据模型、CRUD、item 校验、原子持久化。

一个 ``study:<主题>`` 对象下可有多个**子条目**（entries），每个子条目含若干
**多媒体项**（items）。item 类型枚举：

* ``zotero``   —— 一篇论文（``itemKey`` 8 位大写 + 可选标题）
* ``repo``     —— 代码仓库（``provider`` github|huggingface、``url``、克隆后的
  ``local_path`` / ``cloned_at`` / 可选 ``branch``）
* ``videos``   —— 一条视频链接（``url`` + 可选 ``title``）
* ``websites`` —— 一条网站链接（``url`` + 可选 ``title``）

一个子条目可同时含多种类型的多个 item。

## 存储

``$HERMES_HOME/plugin-data/wb-learn/study-entries.json``，结构：

``{ "<object_id>": { "entries": [ {id,title,note?,created_at,updated_at,items:[...]} ] } }``

只写插件自己的数据目录，**不碰用户资料**。写入一律临时文件 + ``os.replace`` 原子替换；
文件损坏时**不静默清空**：先备份成 ``.bak`` 再抛 ``StudyStoreError``（可恢复）。

## 安全边界

* 只接受 ``study:`` 前缀对象（``catalog.kind_of`` 校验），名称段过 ``paths.safe_child_name``；
* repo url 走主机白名单（github.com / huggingface.co）+ owner/repo 形状校验；
* zotero key 必须 8 位大写字母数字；
* videos / websites 只接受 http/https、有 host、不带凭据。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import catalog, links, paths

STUDY_ENTRIES_NAME = "study-entries.json"

ITEM_TYPES = ("zotero", "repo", "videos", "websites")
REPO_PROVIDERS = ("github", "huggingface")

_COLLECTION_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")
# owner/repo 形状：GitHub 与 HF 都用 namespace/name，段内允许字母数字与 . _ -
_OWNER_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,38}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,100}$")

_LOCK = threading.Lock()


class StudyStoreError(Exception):
    """study-entries.json 损坏或形状非法（保留 .bak，可恢复）。"""


# ---------------------------------------------------------------- 文件位置与读写
def entries_file() -> Path:
    return catalog.store_dir() / STUDY_ENTRIES_NAME


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
    """读整个文件。缺失返回 ``{}``；损坏则备份 ``.bak`` 并抛 ``StudyStoreError``。"""
    path = entries_file()
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
        raise StudyStoreError(
            f"study-entries.json 损坏，已备份到 {backup}：{exc}") from exc
    return data if isinstance(data, dict) else {}


def save_all(data: Dict[str, Any]) -> None:
    _atomic_write(entries_file(),
                  json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:10]


# ---------------------------------------------------------------- 对象校验
def _check_object(object_id: str) -> str:
    if not isinstance(object_id, str) or not object_id.strip():
        raise paths.PathNotAllowed("缺少对象 id（形如 study:RL教学）")
    object_id = object_id.strip()
    try:
        catalog.kind_of(object_id)
    except paths.PathNotAllowed:
        raise
    if not object_id.startswith(catalog.STUDY_PREFIX):
        raise paths.PathNotAllowed("子条目只支持 study: 前缀的自主学习对象")
    return object_id


def object_root(object_id: str) -> Path:
    """对象材料根目录（用于 clone 目标 code/ 的基准，不做存在性判断）。"""
    _check_object(object_id)
    return catalog.object_dir(object_id)


# ---------------------------------------------------------------- item 校验
def _validate_zotero_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    item_key = str(raw.get("itemKey") or raw.get("item_key") or "").strip().upper()
    if not _COLLECTION_KEY_RE.match(item_key):
        raise paths.PathNotAllowed("Zotero item key 形状非法（8 位大写字母数字）")
    title = raw.get("title")
    out = {"id": _new_id("i"), "type": "zotero", "itemKey": item_key}
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:200]
    return out


def _validate_repo_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    provider = str(raw.get("provider") or "").strip().lower()
    if provider not in REPO_PROVIDERS:
        raise paths.PathNotAllowed("repo provider 只接受 github / huggingface")
    url = str(raw.get("url") or "").strip()
    owner, repo = _parse_repo_url(provider, url)
    out = {
        "id": _new_id("i"),
        "type": "repo",
        "provider": provider,
        "url": url,
        "owner": owner,
        "repo": repo,
        "local_path": "",
        "cloned_at": None,
    }
    branch = raw.get("branch")
    if isinstance(branch, str) and branch.strip():
        out["branch"] = branch.strip()[:100]
    # 允许前端回传已克隆状态（clone-repo 成功后由后端补全，这里只做形状校验）
    lp = raw.get("local_path")
    if isinstance(lp, str) and lp.strip():
        out["local_path"] = lp.strip()
    ca = raw.get("cloned_at")
    if isinstance(ca, str) and ca.strip():
        out["cloned_at"] = ca.strip()
    return out


def _parse_repo_url(provider: str, url: str) -> tuple:
    """按 provider 校验仓库 URL，返回 ``(owner, repo)``。"""
    if not url or len(url) > 200:
        raise paths.PathNotAllowed("repo url 不能为空")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise paths.PathNotAllowed("repo url 必须是 https")
    host = parsed.hostname.lower()
    if provider == "github":
        if host != "github.com":
            raise paths.PathNotAllowed("github provider 只接受 github.com")
        got = links.parse_github_url(url)
        if got is None:
            raise paths.PathNotAllowed("github url 形状非法（需 github.com/<owner>/<repo>）")
        return got
    # huggingface
    if host != "huggingface.co":
        raise paths.PathNotAllowed("huggingface provider 只接受 huggingface.co")
    if parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise paths.PathNotAllowed("huggingface url 不允许端口/凭据/query/fragment")
    segs = [s for s in parsed.path.split("/") if s]
    if len(segs) != 2:
        raise paths.PathNotAllowed("huggingface url 需 huggingface.co/<owner>/<repo>")
    owner, repo = segs
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo):
        raise paths.PathNotAllowed("huggingface owner/repo 形状非法")
    return owner, repo


def _validate_link_item(raw: Dict[str, Any], kind: str) -> Dict[str, Any]:
    url = str(raw.get("url") or "").strip()
    if not url or len(url) > 400:
        raise paths.PathNotAllowed("链接 url 不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise paths.PathNotAllowed("链接只接受 http/https 且必须有 host")
    if parsed.username or parsed.password:
        raise paths.PathNotAllowed("链接不允许带凭据")
    title = raw.get("title")
    out = {"id": _new_id("i"), "type": kind, "url": url}
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:200]
    return out


def normalize_item(raw: Any) -> Dict[str, Any]:
    """把一条原始 item 归一成带 id 的标准形状；类型非法则拒绝。"""
    if not isinstance(raw, dict):
        raise paths.PathNotAllowed("item 必须是对象")
    kind = str(raw.get("type") or "").strip()
    if kind == "zotero":
        return _validate_zotero_item(raw)
    if kind == "repo":
        return _validate_repo_item(raw)
    if kind == "videos":
        return _validate_link_item(raw, "videos")
    if kind == "websites":
        return _validate_link_item(raw, "websites")
    raise paths.PathNotAllowed(f"未知 item 类型：{kind!r}（只接受 {ITEM_TYPES}）")


def _normalize_items(raw_items: Any) -> List[Dict[str, Any]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise paths.PathNotAllowed("items 必须是数组")
    if len(raw_items) > 200:
        raise paths.PathNotAllowed("单条目 items 最多 200 条")
    return [normalize_item(it) for it in raw_items]


# ---------------------------------------------------------------- 条目读写
def get_entries(object_id: str) -> List[Dict[str, Any]]:
    """返回对象的子条目列表（无则空列表）。损坏文件会抛 ``StudyStoreError``。"""
    _check_object(object_id)
    data = load_all()
    entry = data.get(object_id)
    if not isinstance(entry, dict):
        return []
    entries = entry.get("entries")
    return entries if isinstance(entries, list) else []


def _find_entry(entries: List[Dict[str, Any]], entry_id: str) -> Optional[Dict[str, Any]]:
    for e in entries:
        if e.get("id") == entry_id:
            return e
    return None


def create_entry(object_id: str, title: str,
                 note: Optional[str] = None,
                 items: Any = None) -> Dict[str, Any]:
    _check_object(object_id)
    if not isinstance(title, str) or not title.strip():
        raise paths.PathNotAllowed("子条目标题必填")
    norm_items = _normalize_items(items)
    entry = {
        "id": _new_id("e"),
        "title": title.strip()[:200],
        "created_at": _now(),
        "updated_at": _now(),
        "items": norm_items,
    }
    if isinstance(note, str) and note.strip():
        entry["note"] = note.strip()[:2000]
    with _LOCK:
        data = load_all()
        bucket = data.setdefault(object_id, {"entries": []})
        entries = bucket.setdefault("entries", [])
        if not isinstance(entries, list):
            bucket["entries"] = []
            entries = bucket["entries"]
        entries.append(entry)
        save_all(data)
    return entry


def update_entry(object_id: str, entry_id: str, *,
                 title: Optional[str] = None,
                 note: Optional[str] = None,
                 items: Any = None,
                 add_items: Any = None) -> Dict[str, Any]:
    """更新子条目。``items`` 给定则整体替换；``add_items`` 给定则追加。"""
    _check_object(object_id)
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise paths.PathNotAllowed("缺少 entry_id")
    with _LOCK:
        data = load_all()
        bucket = data.setdefault(object_id, {"entries": []})
        entries = bucket.setdefault("entries", [])
        entry = _find_entry(entries, entry_id.strip())
        if entry is None:
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        if title is not None:
            if not isinstance(title, str) or not title.strip():
                raise paths.PathNotAllowed("子条目标题不能为空")
            entry["title"] = title.strip()[:200]
        if note is not None:
            if note is None or (isinstance(note, str) and not note.strip()):
                entry.pop("note", None)
            elif isinstance(note, str):
                entry["note"] = note.strip()[:2000]
            else:
                raise paths.PathNotAllowed("note 必须是字符串")
        if items is not None:
            entry["items"] = _normalize_items(items)
        if add_items is not None:
            extra = _normalize_items(add_items)
            entry.setdefault("items", []).extend(extra)
        entry["updated_at"] = _now()
        save_all(data)
    return entry


def delete_entry(object_id: str, entry_id: str) -> Dict[str, Any]:
    _check_object(object_id)
    with _LOCK:
        data = load_all()
        bucket = data.get(object_id)
        entries = bucket.get("entries") if isinstance(bucket, dict) else None
        if not isinstance(entries, list):
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        entry = _find_entry(entries, entry_id)
        if entry is None:
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        entries.remove(entry)
        if not entries and isinstance(bucket, dict):
            data.pop(object_id, None)
        save_all(data)
    return {"ok": True, "deleted": entry_id}


def delete_item(object_id: str, entry_id: str, item_id: str) -> Dict[str, Any]:
    _check_object(object_id)
    with _LOCK:
        data = load_all()
        bucket = data.get(object_id)
        entries = bucket.get("entries") if isinstance(bucket, dict) else None
        if not isinstance(entries, list):
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        entry = _find_entry(entries, entry_id)
        if entry is None:
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        items = entry.get("items") or []
        target = None
        for it in items:
            if it.get("id") == item_id:
                target = it
                break
        if target is None:
            raise paths.PathNotAllowed(f"item 不存在：{item_id}")
        items.remove(target)
        entry["updated_at"] = _now()
        save_all(data)
    return {"ok": True, "deleted": item_id}


def mark_repo_cloned(object_id: str, entry_id: str, item_id: str,
                     local_path: str, cloned_at: Optional[str] = None) -> Dict[str, Any]:
    """clone-repo 成功后回写 item 的克隆状态（local_path / cloned_at）。"""
    _check_object(object_id)
    with _LOCK:
        data = load_all()
        bucket = data.get(object_id)
        entries = bucket.get("entries") if isinstance(bucket, dict) else None
        if not isinstance(entries, list):
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        entry = _find_entry(entries, entry_id)
        if entry is None:
            raise paths.PathNotAllowed(f"子条目不存在：{entry_id}")
        for it in entry.get("items") or []:
            if it.get("id") == item_id:
                it["local_path"] = local_path
                it["cloned_at"] = cloned_at or _now()
                entry["updated_at"] = _now()
                save_all(data)
                return it
        raise paths.PathNotAllowed(f"item 不存在：{item_id}")


def find_item(object_id: str, item_id: str) -> Optional[Dict[str, Any]]:
    """跨子条目查找一个 item（点击记录 / open 用）。"""
    for entry in get_entries(object_id):
        for it in entry.get("items") or []:
            if it.get("id") == item_id:
                return it
    return None
