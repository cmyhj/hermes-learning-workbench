"""只读外链：Zotero collections 与 GitHub 仓库。

## 两条硬约束
1. **只读**。Zotero 只走本机 ``http://127.0.0.1:23119/api/users/0`` 的 GET；
   GitHub 只走 ``gh api``/HTTPS GET。本模块没有任何写函数，也不调用 push/clone/commit。
2. **不能是任意 URL（防 SSRF）**。GitHub 目标只接受两种来源：
   * 本地 ``<对象>/.git/config`` 里的 origin remote（只读文件发现）；
   * ``plugin-data/wb-learn/links.json`` 里显式登记的 ``https://github.com/<owner>/<repo>``。
   两者都过 ``parse_github_url`` 白名单校验：host 必须是 github.com，路径必须恰好
   ``owner/repo``，不允许 query/fragment/端口/凭据。其它主机一律拒绝。

## 错误必须可见
离线、gh 未登录、Zotero 未开、仓库 404 —— 全部作为 ``status`` / ``error`` 字段返回，
不吞成空数组，也不假装「没有关联」。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import catalog, paths

ZOTERO_BASE = os.environ.get("WB_LEARN_ZOTERO_BASE") or "http://127.0.0.1:23119/api/users/0"
ZOTERO_TIMEOUT = 4.0
GH_TIMEOUT = 12
LINKS_NAME = "links.json"

_GH_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9][\w.-]{0,38})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")
_COLLECTION_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


# --------------------------------------------------------------------------
# links.json（对象 → 外链登记）
# --------------------------------------------------------------------------
def links_file() -> Path:
    return catalog.store_dir() / LINKS_NAME


def load_links() -> Dict[str, Any]:
    path = links_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _object_links(object_id: str) -> Dict[str, Any]:
    entry = load_links().get(object_id)
    return entry if isinstance(entry, dict) else {}


def save_object_links(object_id: str, *, zotero: Any = ..., github: Any = ...) -> Dict[str, Any]:
    """保存对象的只读外链登记。``...`` = 不改动该字段；``None`` = 清除。"""
    if catalog.get_object(object_id) is None:
        raise paths.PathNotAllowed("未知学习对象 id")

    mapping = load_links()
    entry = mapping.get(object_id) if isinstance(mapping.get(object_id), dict) else {}

    if zotero is not ...:
        if zotero is None:
            entry.pop("zotero", None)
        else:
            key = str(zotero).strip()
            if not _COLLECTION_KEY_RE.match(key):
                raise paths.PathNotAllowed("Zotero collection key 形状非法（8 位大写字母数字）")
            known = {c["key"] for c in zotero_collections().get("collections", [])}
            if known and key not in known:
                raise paths.PathNotAllowed("该 collection key 不在当前 Zotero 库中")
            entry["zotero"] = {"collection": key}
    if github is not ...:
        if github is None:
            entry.pop("github", None)
        else:
            parsed = parse_github_url(str(github))
            if parsed is None:
                raise paths.PathNotAllowed("只接受 https://github.com/<owner>/<repo>")
            entry["github"] = {"repo": f"{parsed[0]}/{parsed[1]}", "source": "manual"}

    entry["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    mapping[object_id] = entry
    links_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = links_file().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, links_file())
    return {"ok": True, "links": entry}


def parse_github_url(url: str) -> Optional[Tuple[str, str]]:
    """``https://github.com/owner/repo[.git]`` → ``(owner, repo)``；其它一律 None。"""
    url = (url or "").strip()
    if not url or len(url) > 200:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None
    if parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    match = _GH_URL_RE.match(url.rstrip("/"))
    if not match:
        return None
    return match.group(1), match.group(2)


# --------------------------------------------------------------------------
# Zotero（只读）
# --------------------------------------------------------------------------
def _zotero_get(path: str) -> Tuple[Optional[Any], Optional[str]]:
    url = f"{ZOTERO_BASE}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=ZOTERO_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as exc:
        return None, f"Zotero 本地 API 返回 {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return None, f"Zotero 本地 API 不可达：{type(exc).__name__}（Zotero 是否在运行？）"
    except json.JSONDecodeError:
        return None, "Zotero 返回的不是 JSON"


def zotero_collections() -> Dict[str, Any]:
    """列出 collections（只读）。离线/未开就如实报 error，不返回伪造的空成功。"""
    data, error = _zotero_get("collections")
    if error:
        return {"available": False, "error": error, "base": ZOTERO_BASE,
                "collections": [], "count": 0}
    items: List[Dict[str, Any]] = []
    for raw in data if isinstance(data, list) else []:
        try:
            inner = raw.get("data") or {}
            key = str(raw.get("key") or inner.get("key") or "")
            if not _COLLECTION_KEY_RE.match(key):
                continue
            parent = inner.get("parentCollection")
            items.append({
                "key": key,
                "name": str(inner.get("name") or "").strip() or key,
                "parent": parent if isinstance(parent, str) and parent else None,
                "items": (raw.get("meta") or {}).get("numItems"),
            })
        except AttributeError:
            continue
    items.sort(key=lambda c: c["name"].lower())
    return {"available": True, "error": None, "base": ZOTERO_BASE,
            "count": len(items), "collections": items}


def zotero_collection_items(key: str, *, limit: int = 30) -> Dict[str, Any]:
    """某 collection 前 N 条的**元数据**（标题/作者/年份），不下载 PDF、不写批注。"""
    if not _COLLECTION_KEY_RE.match(key or ""):
        raise paths.PathNotAllowed("collection key 形状非法")
    data, error = _zotero_get(f"collections/{key}/items/top?limit={max(1, min(limit, 50))}")
    if error:
        return {"available": False, "error": error, "collection": key, "items": []}
    out = []
    for raw in data if isinstance(data, list) else []:
        inner = raw.get("data") or {}
        creators = inner.get("creators") or []
        names = [str(c.get("lastName") or c.get("name") or "").strip() for c in creators if isinstance(c, dict)]
        out.append({
            "key": raw.get("key"),
            "title": str(inner.get("title") or "").strip() or "（无标题）",
            "authors": [n for n in names if n][:4],
            "date": str(inner.get("date") or "")[:10],
            "itemType": inner.get("itemType"),
            "doi": (inner.get("DOI") or None),
        })
    return {"available": True, "error": None, "collection": key, "count": len(out), "items": out}


def resolve_study_collection(object_id: str, *, collections: Optional[Dict[str, Any]] = None,
                            requested_key: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the paper collection for a study, without persisting inferred choices."""
    entry = _object_links(object_id)
    configured = ((entry.get("zotero") or {}).get("collection")
                  if isinstance(entry.get("zotero"), dict) else None)
    found = collections if collections is not None else zotero_collections()
    base = {"status": "unavailable", "configured_key": configured,
            "collection": None, "choices": [], "error": found.get("error")}
    if not found.get("available"):
        return base
    all_collections = found.get("collections") or []
    by_key = {c.get("key"): c for c in all_collections if isinstance(c, dict)}
    if configured:
        if configured not in by_key:
            return {**base, "status": "configured_missing",
                    "error": "登记的 collection 已不在当前 Zotero 库中（未静默改绑）"}
        return {**base, "status": "configured", "collection": by_key[configured],
                "choices": [by_key[configured]], "error": None}
    obj = catalog.get_object(object_id) or {}
    project_name = str(obj.get("name") or "").strip()
    exact = [c for c in all_collections if c.get("name") == project_name]
    for c in exact:
        parts, current, seen = [str(c.get("name") or "")], c, set()
        while current.get("parent") and current["parent"] in by_key and current["parent"] not in seen:
            seen.add(current["parent"])
            current = by_key[current["parent"]]
            parts.insert(0, str(current.get("name") or ""))
        c["path"] = " / ".join(parts)
    if len(exact) == 1:
        return {**base, "status": "auto", "collection": exact[0],
                "choices": exact, "error": None}
    if len(exact) > 1:
        allowed = {c.get("key") for c in exact}
        if requested_key in allowed:
            selected = next(c for c in exact if c.get("key") == requested_key)
            return {**base, "status": "chosen", "collection": selected,
                    "choices": exact, "error": None}
        return {**base, "status": "ambiguous", "choices": exact,
                "error": "有多个同名 Zotero collection，请选择路径"}
    return {**base, "status": "none", "error": "没有同名 Zotero collection；请在 Zotero 设置中登记 collection"}


def zotero_item_in_collection(item_key: str, collection_key: str) -> bool:
    """Check actual direct collection membership using Zotero item metadata."""
    if not _COLLECTION_KEY_RE.match(item_key or "") or not _COLLECTION_KEY_RE.match(collection_key or ""):
        return False
    data, error = _zotero_get(f"items/{item_key}")
    if error or not isinstance(data, dict):
        return False
    inner = data.get("data") or {}
    memberships = inner.get("collections") or []
    return collection_key in memberships


def zotero_item(key: str) -> Dict[str, Any]:
    """读单条 Zotero 文献的**元数据**（标题/摘要/DOI/arXiv/extra/url）。只读。

    用于「一键从论文找代码仓库」：给 ``itemKey``，拿标题/摘要关键词与 DOI/arXiv 号
    去 GitHub 搜索。不下载 PDF、不写批注、不改动 Zotero。
    """
    if not _COLLECTION_KEY_RE.match(key or ""):
        raise paths.PathNotAllowed("item key 形状非法（8 位大写字母数字）")
    data, error = _zotero_get(f"items/{key}")
    if error:
        return {"available": False, "error": error, "key": key}
    inner = (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        return {"available": False, "error": "Zotero 返回的条目形状无法识别", "key": key}
    extra = str(inner.get("extra") or "")
    arxiv = None
    m = re.search(r"arXiv:\s*([\w./-]+)", extra)
    if m:
        arxiv = m.group(1)
    url = inner.get("url")
    github_url = None
    if isinstance(url, str):
        parsed = parse_github_url(url)
        if parsed:
            github_url = f"https://github.com/{parsed[0]}/{parsed[1]}"
    creators = inner.get("creators") or []
    names = [str(c.get("lastName") or c.get("name") or "").strip() for c in creators if isinstance(c, dict)]
    return {
        "available": True,
        "error": None,
        "key": key,
        "title": str(inner.get("title") or "").strip(),
        "abstract": str(inner.get("abstractNote") or "").strip(),
        "doi": inner.get("DOI") or None,
        "arxiv": arxiv,
        "url": url if isinstance(url, str) else None,
        "github_url": github_url,
        "authors": [n for n in names if n][:4],
        "item_type": inner.get("itemType"),
    }


# --------------------------------------------------------------------------
# GitHub（只读）
# --------------------------------------------------------------------------
def _gh_available() -> bool:
    return bool(shutil.which("gh"))


def _gh_api_json(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """``gh api <path>``（沿用本机 gh 的登录态）；失败把 gh 的错误原样带出。"""
    exe = shutil.which("gh")
    if not exe:
        return None, "gh CLI 未安装：只能读取公开仓库元数据（走匿名 HTTPS）"
    try:
        proc = subprocess.run(
            [exe, "api", path, "-H", "Accept: application/vnd.github+json"],
            capture_output=True, text=True, timeout=GH_TIMEOUT, encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh 调用失败：{type(exc).__name__}"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, f"gh api 失败（退出码 {proc.returncode}）：{msg[-1] if msg else '无输出'}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, "gh 输出不是 JSON"


def _anon_api_json(path: str) -> Tuple[Optional[Any], Optional[str]]:
    url = f"https://api.github.com/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "wb-learn/2.0 (read-only)",
    })
    try:
        with urllib.request.urlopen(req, timeout=GH_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as exc:
        hint = {403: "可能触发未认证限流", 404: "仓库不存在或无权访问"}.get(exc.code, "")
        return None, f"GitHub API {exc.code} {hint}".strip()
    except (urllib.error.URLError, OSError) as exc:
        return None, f"GitHub 不可达：{type(exc).__name__}"
    except json.JSONDecodeError:
        return None, "GitHub 返回的不是 JSON"


def github_repo(owner: str, repo: str) -> Dict[str, Any]:
    """仓库只读元数据 + 最近提交。不 clone、不 fetch、不改任何远端状态。"""
    if not (re.fullmatch(r"[\w.-]{1,39}", owner or "") and re.fullmatch(r"[\w.-]{1,100}", repo or "")):
        raise paths.PathNotAllowed("owner/repo 形状非法")
    path = f"repos/{owner}/{repo}"
    data, error = _gh_api_json(path)
    via = "gh"
    if error and _gh_available():
        return {"ok": False, "error": error, "via": via, "repo": f"{owner}/{repo}"}
    if error:
        data, error = _anon_api_json(path)
        via = "anonymous-https"
    if error:
        return {"ok": False, "error": error, "via": via, "repo": f"{owner}/{repo}"}

    commits: List[Dict[str, Any]] = []
    commit_data, commit_err = (_gh_api_json(f"repos/{owner}/{repo}/commits?per_page=5")
                               if via == "gh" else _anon_api_json(f"repos/{owner}/{repo}/commits?per_page=5"))
    if not commit_err and isinstance(commit_data, list):
        for c in commit_data[:5]:
            commit = c.get("commit") or {}
            commits.append({
                "sha": str(c.get("sha") or "")[:10],
                "message": str((commit.get("message") or "").splitlines()[0])[:160],
                "date": ((commit.get("author") or {}).get("date") or "")[:19],
            })
    return {
        "ok": True,
        "via": via,
        "repo": f"{owner}/{repo}",
        "url": data.get("html_url") or f"https://github.com/{owner}/{repo}",
        "description": (data.get("description") or "")[:400] or None,
        "stars": data.get("stargazers_count"),
        "language": data.get("language"),
        "license": ((data.get("license") or {}).get("spdx_id")),
        "pushed_at": data.get("pushed_at"),
        "archived": data.get("archived"),
        "private": data.get("private"),
        "default_branch": data.get("default_branch"),
        "commits": commits,
        "commit_error": commit_err,
        "read_only": True,
    }


def resolve_github(object_id: str) -> Dict[str, Any]:
    """对象 → 仓库候选：显式登记优先，其次本地 .git origin。都校验过白名单。"""
    entry = _object_links(object_id)
    configured = (entry.get("github") or {}).get("repo") if isinstance(entry.get("github"), dict) else None
    local = (catalog.get_object(object_id) or {}).get("git") or {}
    local_url = local.get("remote_url")

    candidates: List[Dict[str, Any]] = []
    if configured:
        parsed = parse_github_url(f"https://github.com/{configured}")
        if parsed:
            candidates.append({"repo": f"{parsed[0]}/{parsed[1]}", "source": "manual"})
    if local_url:
        parsed = parse_github_url(local_url if local_url.startswith("http") else _ssh_to_https(local_url))
        if parsed:
            repo = f"{parsed[0]}/{parsed[1]}"
            if not any(c["repo"] == repo for c in candidates):
                candidates.append({"repo": repo, "source": "git-remote"})

    chosen = candidates[0] if candidates else None
    out: Dict[str, Any] = {
        "object_id": object_id,
        "local_remote": local_url,
        "local_remote_accepted": bool(local_url and parse_github_url(
            local_url if local_url.startswith("http") else _ssh_to_https(local_url))),
        "configured": configured,
        "candidates": candidates,
        "repo": chosen["repo"] if chosen else None,
        "source": chosen["source"] if chosen else None,
        "detail": None,
    }
    if chosen:
        owner, repo = chosen["repo"].split("/", 1)
        out["detail"] = github_repo(owner, repo)
    return out


def _ssh_to_https(url: str) -> str:
    """``git@github.com:owner/repo.git`` → ``https://github.com/owner/repo``（只为校验）。"""
    match = re.match(r"^git@github\.com:([\w.-]+)/([\w.-]+?)(?:\.git)?$", (url or "").strip())
    if not match:
        return url
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


def object_links(object_id: str) -> Dict[str, Any]:
    """UI 用：对象的 Zotero 与 GitHub 关联现状（含不可用原因）。"""
    if catalog.get_object(object_id) is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    entry = _object_links(object_id)
    collections = zotero_collections()
    key = (entry.get("zotero") or {}).get("collection") if isinstance(entry.get("zotero"), dict) else None
    resolution = resolve_study_collection(object_id, collections=collections)
    zotero_out: Dict[str, Any] = {
        "configured_key": key,
        "resolution": resolution,
        "available": collections["available"],
        "error": collections["error"],
        "collection": None,
        "items": [],
    }
    if resolution.get("collection"):
        zotero_out["collection"] = resolution["collection"]
        zotero_out["items"] = zotero_collection_items(resolution["collection"]["key"]).get("items", [])
    elif resolution.get("error"):
        zotero_out["error"] = resolution["error"]
    return {
        "object_id": object_id,
        "zotero": zotero_out,
        "zotero_collections": collections,
        "github": resolve_github(object_id),
        "links_file": str(links_file()),
        "read_only": True,
        "policy": "只读：不写 Zotero、不 push/fetch GitHub；仓库地址只接受 github.com/<owner>/<repo>。",
    }


# --------------------------------------------------------------------------
# TextFold：把 Zotero 条目送进折读（1–5 级）
# --------------------------------------------------------------------------
TEXTFOLD_BASE = os.environ.get("WB_LEARN_TEXTFOLD_BASE") or "http://127.0.0.1:3017"
_ARXIV_IN_EXTRA = re.compile(r"arXiv:\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.I)
_ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.I)


def _textfold(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 120) -> Tuple[Optional[Any], Optional[str]]:
    url = f"{TEXTFOLD_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as exc:
        return None, f"TextFold 返回 {exc.code}"
    except (urllib.error.URLError, OSError):
        return None, "TextFold 不可达：确认 127.0.0.1:3017 在运行（Hermes 插件「阅读」页可一键启动）"
    except json.JSONDecodeError:
        return None, "TextFold 返回的不是 JSON"


def _paper_ref(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """条目 → (引用串, 类型)：优先 DOI，其次 extra 或 url 里的 arXiv 号。"""
    inner = (item or {}).get("data") or {}
    doi = str(inner.get("DOI") or "").strip()
    if doi:
        return "https://doi.org/" + doi, "doi"
    found = _ARXIV_IN_EXTRA.search(str(inner.get("extra") or ""))
    if found:
        return found.group(1), "arxiv"
    found = _ARXIV_IN_URL.search(str(inner.get("url") or ""))
    if found:
        return found.group(1), "arxiv"
    return None, None


def _no_redirect_open(url: str, timeout: float = 8):
    """拿 Zotero 附件文件的 302 Location（file:// 本地路径），不跟随跳转。"""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url), timeout=timeout) as resp:
            return resp.headers.get("Location"), None
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return exc.headers.get("Location"), None
        return None, f"Zotero 附件返回 {exc.code}"
    except (urllib.error.URLError, OSError):
        return None, "Zotero 不可达"


def _zotero_pdf_path(key: str) -> Tuple[Optional[str], Optional[str]]:
    """条目第一条 PDF 附件的本地路径（Zotero 的 file:// 302）。"""
    kids, error = _zotero_get(f"items/{key}/children")
    if error:
        return None, error
    for child in kids if isinstance(kids, list) else []:
        inner = child.get("data") or {}
        if inner.get("itemType") != "attachment":
            continue
        name = str(inner.get("filename") or "")
        if not name.lower().endswith(".pdf"):
            continue
        location, err = _no_redirect_open(f"{ZOTERO_BASE}/items/{child.get('key')}/file")
        if err or not location:
            continue
        path = urllib.parse.unquote(urllib.parse.urlparse(location).path)
        if path.startswith("/") and re.match(r"^/[A-Za-z]:", path):
            path = path[1:]          # /E:/… → E:/…
        if os.path.isfile(path):
            return path, None
    return None, "这个条目没有本地 PDF 附件"


def _textfold_pdf(raw: bytes, name: str, lang: str) -> Tuple[Optional[Any], Optional[str]]:
    req = urllib.request.Request(
        f"{TEXTFOLD_BASE}/api/files", data=raw,
        headers={"Content-Type": "application/pdf",
                 "X-Name": name.encode("utf-8").decode("latin-1", "ignore") or "paper.pdf",
                 "X-Lang": lang})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as exc:
        return None, f"TextFold 返回 {exc.code}"
    except (urllib.error.URLError, OSError):
        return None, "TextFold 不可达：确认 127.0.0.1:3017 在运行"


def zotero_read(key: str, *, lang: str = "zh") -> Dict[str, Any]:
    """把一条 Zotero 文献交给 TextFold 折读：先按 DOI/arXiv 取线上版，失败再用本地 PDF 附件。"""
    if not _COLLECTION_KEY_RE.match((key or "").upper()):
        raise paths.PathNotAllowed("Zotero key 形状非法")
    key = key.upper()
    data, error = _zotero_get(f"items/{key}")
    if error:
        return {"ok": False, "error": error}
    inner = (data or {}).get("data") or {}
    title = str(inner.get("title") or "").strip()

    ref, kind = _paper_ref(data if isinstance(data, dict) else {})
    online_error = None
    if ref:
        job, err = _textfold("/api/generate", {"paper": ref, "lang": lang})
        if not err:
            return {"ok": True, "via": kind, "ref": ref, "title": title, "lang": lang, **(job or {})}
        online_error = err

    path, path_error = _zotero_pdf_path(key)
    if not path:
        return {"ok": False,
                "error": (f"线上取不到（{online_error}）；" if online_error else "") + (path_error or "没有 PDF 附件")}
    raw = Path(path).read_bytes()
    if len(raw) > 80 * 1024 * 1024:
        return {"ok": False, "error": "PDF 超过 80 MB，未发送"}
    res, err = _textfold_pdf(raw, os.path.basename(path), lang)
    if err:
        return {"ok": False, "error": err}
    if isinstance(res, dict) and res.get("needs") == "engine":
        return {"ok": False, "error": "TextFold 内置解析读不了这份 PDF（需要它的 PDF 引擎）"}
    return {"ok": True, "via": "attachment", "file": os.path.basename(path), "title": title,
            "lang": lang, **(res or {})}


def textfold_job(job_id: str) -> Dict[str, Any]:
    """转发 TextFold 的 job 状态，供前端轮询。"""
    if not re.match(r"^[A-Za-z0-9]{4,40}$", job_id or ""):
        raise paths.PathNotAllowed("job id 形状非法")
    jobs, err = _textfold("/api/jobs", timeout=15)
    if err:
        return {"ok": False, "error": err}
    for job in jobs if isinstance(jobs, list) else []:
        if job.get("id") == job_id:
            return {"ok": True, "job": job}
    return {"ok": False, "error": "没有这个 job"}
