"""仓库操作：从论文找代码仓库、克隆、用 VSCode 打开。

三件事，全部**显式确认、绝不自动**：

* ``resolve_repo_candidates`` —— 给 Zotero ``itemKey``，读文献元数据（标题/摘要/DOI/
  arXiv），用 ``gh search repos`` 搜 GitHub，返回**候选列表**（owner/repo、stars、
  描述、url）。绝不自动选定、绝不自动克隆；找不到如实返回 ``not_found`` 与已尝试的
  查询串。若条目 ``url``/``extra`` 里有 github.com 链接，作为第一候选并标注来源。
* ``clone_repo`` —— 用户确认候选后克隆。目标目录固定 ``<对象根>/code/<owner>__<repo>``，
  禁止目录穿越、禁止覆盖已存在目录；``git clone --depth 1``，先走 http.proxy
  ``127.0.0.1:7890``，失败重试一次直连；成功后回读 ``.git`` 校验。
* ``open_in_vscode`` —— 后端用 VSCode 打开已克隆仓库。仅允许 ``local_path`` 存在、
  realpath 落在对象根内、且是 git 仓库（含 ``.git`` 或 HF 目录标志）的路径；
  通过 ``subprocess`` 以列表参数调用 VSCode CLI，不拼 shell、不用 ``shell=True``。

## 安全边界

* owner/repo 形状校验 + host 白名单（github.com / huggingface.co），不接受任意 URL；
* clone 目标只允许落在对象根下的 ``code/`` 子目录；
* open 只允许对象根内的 git 仓库路径，越界/非 git 一律拒绝。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import catalog, links, paths, study

CODE_DIR_NAME = "code"
GIT_PROXY = os.environ.get("WB_LEARN_GIT_PROXY") or "http://127.0.0.1:7890"
GH_SEARCH_LIMIT = 10
GH_TIMEOUT = 20
VSCODE_CANDIDATES = (
    r"D:/DevEnvironments/IDE/Microsoft VS Code/Code.exe",
    "Code.exe",
    "code.exe",
)

_STOPWORDS = {
    "a", "an", "the", "of", "for", "with", "and", "or", "to", "in", "on", "by",
    "via", "using", "based", "from", "toward", "towards", "into", "over", "under",
}


# ---------------------------------------------------------------- 可注入执行器
def _run_gh_search(query: str, limit: int = GH_SEARCH_LIMIT) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """``gh search repos`` 搜索。返回 ``(candidates, error)``。可被测试注入替换。"""
    exe = shutil.which("gh")
    if not exe:
        return None, "gh CLI 未安装：无法搜索仓库"
    try:
        proc = subprocess.run(
            [exe, "search", "repos", query, "--limit", str(limit),
             "--json", "fullName,stargazersCount,description,htmlUrl,language,updatedAt"],
            capture_output=True, text=True, timeout=GH_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh 搜索失败：{type(exc).__name__}"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, f"gh search 失败（退出码 {proc.returncode}）：{msg[-1] if msg else '无输出'}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "gh 搜索输出不是 JSON"
    out: List[Dict[str, Any]] = []
    for r in data if isinstance(data, list) else []:
        full = str(r.get("fullName") or "")
        if "/" not in full:
            continue
        owner, repo = full.split("/", 1)
        out.append({
            "owner": owner,
            "repo": repo,
            "full_name": full,
            "url": r.get("htmlUrl") or f"https://github.com/{full}",
            "stars": r.get("stargazersCount"),
            "description": (r.get("description") or "")[:300] or None,
            "language": r.get("language"),
        })
    return out, None


def _run_git(args: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """执行 git 命令（列表参数，无 shell）。返回 ``(rc, stdout, stderr)``。可注入。"""
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, timeout=300,
                              encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", f"git 调用失败：{type(exc).__name__}"


# ---------------------------------------------------------------- resolve-repo
def _title_query(title: str) -> str:
    """从标题构造搜索关键词：去停用词、保留字母数字，截断到 ~6 词。"""
    words = re.findall(r"[A-Za-z0-9][\w-]*", title or "")
    keep = [w for w in words if w.lower() not in _STOPWORDS and len(w) > 1]
    return " ".join(keep[:6])


def resolve_repo_candidates(item_key: str) -> Dict[str, Any]:
    """从 Zotero 文献找代码仓库候选。只读，绝不自动选定/克隆。"""
    if not re.match(r"^[A-Z0-9]{8}$", item_key or ""):
        raise paths.PathNotAllowed("Zotero item key 形状非法（8 位大写字母数字）")
    item = links.zotero_item(item_key)
    if not item.get("available"):
        return {"status": "error", "error": item.get("error"),
                "key": item_key, "candidates": [], "queries": [], "item": item}

    queries: List[str] = []
    title_q = _title_query(item.get("title", ""))
    if title_q:
        queries.append(title_q)
    if item.get("doi"):
        queries.append(str(item["doi"]))
    if item.get("arxiv"):
        queries.append(str(item["arxiv"]))

    candidates: List[Dict[str, Any]] = []
    # 条目 url 里的 github.com 链接作为第一候选
    if item.get("github_url"):
        owner, repo = study._parse_repo_url("github", item["github_url"])
        candidates.append({
            "owner": owner, "repo": repo,
            "full_name": f"{owner}/{repo}",
            "url": f"https://github.com/{owner}/{repo}",
            "stars": None, "description": None, "language": None,
            "source": "zotero-url",
        })

    seen = {c["full_name"] for c in candidates}
    for q in queries:
        found, err = _run_gh_search(q)
        if err:
            continue
        for c in found or []:
            if c["full_name"] not in seen:
                c["source"] = "gh-search"
                c["query"] = q
                seen.add(c["full_name"])
                candidates.append(c)

    if not candidates:
        return {"status": "not_found", "key": item_key,
                "candidates": [], "queries": queries, "item": item,
                "error": "没有找到候选仓库（已尝试上述查询）"}
    return {"status": "ok", "key": item_key,
            "candidates": candidates, "queries": queries, "item": item,
            "error": None}


# ---------------------------------------------------------------- clone-repo
def clone_repo(object_id: str, provider: str, url: str) -> Dict[str, Any]:
    """克隆仓库到 ``<对象根>/code/<owner>__<repo>``。用户确认候选后调用。"""
    study._check_object(object_id)
    provider = (provider or "").strip().lower()
    if provider not in study.REPO_PROVIDERS:
        raise paths.PathNotAllowed("provider 只接受 github / huggingface")
    owner, repo = study._parse_repo_url(provider, url)

    root = study.object_root(object_id)
    configured_root = paths.default_roots()["study"]
    if paths.is_link(root) or paths.safe_under(configured_root, root) is None:
        raise paths.PathNotAllowed("自主学习对象目录越过配置根或是链接")
    code_dir = root / CODE_DIR_NAME
    target = code_dir / f"{owner}__{repo}"

    # code/ 可能还不存在（首次克隆）：先建目录再校验，否则 safe_under 的
    # resolve(strict=True) 会因路径不存在而返回 None，把正常克隆误判成越界。
    if code_dir.exists() and paths.is_link(code_dir):
        raise paths.PathNotAllowed("code/ 不能是符号链接或 junction")
    code_dir.mkdir(parents=True, exist_ok=True)
    if paths.is_link(code_dir) or paths.safe_under(root, code_dir) is None:
        raise paths.PathNotAllowed("code/ 路径越过对象根或是链接")

    # 禁止目录穿越：target 必须落在 root/code 之下（已存在时按真实路径校验；
    # 不存在时校验其父目录在 code/ 内且名字未被规范化改变）
    if paths.safe_under(code_dir, target, allow_root=False) is None:
        if target.exists():
            raise paths.PathNotAllowed("克隆目标不在允许的 code/ 目录下")
        if paths.safe_under(code_dir, target.parent, allow_root=True) is None:
            raise paths.PathNotAllowed("克隆目标不在允许的 code/ 目录下")
        if target.name != f"{owner}__{repo}":
            raise paths.PathNotAllowed("克隆目标名称被规范化，拒绝")

    if target.exists() or target.is_symlink():
        return {"status": "exists", "path": str(target),
                "owner": owner, "repo": repo,
                "message": "目标目录已存在，未覆盖"}

    # 先走代理，失败重试一次直连
    attempts = [
        ["-c", f"http.proxy={GIT_PROXY}", "clone", "--depth", "1", url, str(target)],
        ["clone", "--depth", "1", url, str(target)],
    ]
    last_err = ""
    for args in attempts:
        rc, out, err = _run_git(args, cwd=code_dir)
        if rc == 0:
            # 回读 .git 校验
            git_dir = target / ".git"
            if not (git_dir.is_dir() or git_dir.is_file()):
                return {"status": "error", "path": str(target),
                        "error": "克隆命令成功但 .git 缺失，校验失败",
                        "owner": owner, "repo": repo}
            return {"status": "ok", "path": str(target),
                    "owner": owner, "repo": repo,
                    "cloned_at": _dt.datetime.now().isoformat(timespec="seconds"),
                    "via": "proxy" if args[0] == "-c" else "direct"}
        last_err = (err or out or "").strip().splitlines()
        last_err = last_err[-1] if last_err else "无输出"
    return {"status": "error", "path": str(target),
            "error": f"克隆失败（代理与直连均未成功）：{last_err}",
            "owner": owner, "repo": repo}


# ---------------------------------------------------------------- open-in-vscode
def _is_git_repo(path: Path) -> bool:
    git_dir = path / ".git"
    if paths.is_link(git_dir):
        return False
    if git_dir.is_dir() or git_dir.is_file():
        return True
    # HF 目录标志：HF 仓库克隆后可能带 .hf 标记
    return (path / ".hf").exists() or (path / "hf_marker").exists()


def _find_vscode() -> Optional[str]:
    for cand in VSCODE_CANDIDATES:
        if cand == "code":
            continue
        elif Path(cand).name.lower() in ("code.exe",) and "/" not in cand and "\\" not in cand:
            found = shutil.which(cand)
            if found and Path(found).suffix.lower() == ".exe":
                return found
        elif Path(cand).is_file():
            return cand
    return None


def open_in_vscode(object_id: str, local_path: str) -> Dict[str, Any]:
    """用 VSCode 打开已克隆仓库。仅允许对象根内的 git 仓库路径。"""
    study._check_object(object_id)
    if not isinstance(local_path, str) or not local_path.strip():
        raise paths.PathNotAllowed("缺少 local_path")
    root = study.object_root(object_id)
    configured_root = paths.default_roots()["study"]
    if paths.is_link(root) or paths.safe_under(configured_root, root) is None:
        raise paths.PathNotAllowed("自主学习对象目录越过配置根或是链接")
    candidate = Path(local_path.strip())
    # Reject any reparse point along the requested path, even if it resolves back
    # inside the object. This keeps clone/open containment consistent on Windows.
    try:
        lexical = Path(os.path.abspath(candidate))
        lexical.relative_to(root.absolute())
        cursor = root
        for part in lexical.relative_to(root.absolute()).parts:
            cursor = cursor / part
            if cursor.exists() and paths.is_link(cursor):
                raise paths.PathNotAllowed("local_path 包含符号链接或 junction")
    except ValueError:
        pass
    real = paths.safe_under(root, candidate)
    if real is None:
        raise paths.PathNotAllowed("local_path 不在对象根内（拒绝越界打开）")
    if not real.is_dir():
        raise paths.PathNotAllowed("local_path 不是目录")
    if not _is_git_repo(real):
        raise paths.PathNotAllowed("local_path 不是 git 仓库（无 .git / HF 标志）")
    exe = _find_vscode()
    if not exe:
        return {"status": "error", "path": str(real),
                "error": "未找到 VSCode Code.exe（不会执行 Windows batch 文件）"}
    try:
        proc = subprocess.run([exe, str(real)], capture_output=True, text=True,
                              timeout=30, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"status": "error", "path": str(real), "exe": exe,
                "error": f"VSCode 调用失败：{type(exc).__name__}"}
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"status": "error", "path": str(real), "exe": exe,
                "error": f"VSCode 退出码 {proc.returncode}：{msg[-1] if msg else '无输出'}"}
    return {"status": "ok", "path": str(real), "exe": exe}
