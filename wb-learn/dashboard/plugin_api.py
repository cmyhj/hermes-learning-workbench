"""wb-learn — 学习区后端（课程 / 自主学习 + 题库 + 只读外链）。

挂载点：``/api/plugins/wb-learn/``

## 分层
本文件只做 **HTTP 路由 + 参数形状校验**；业务逻辑在 ``wbl_core/``：
``paths`` / ``catalog`` / ``quiz_schema`` / ``quiz_store`` / ``materials`` / ``links``。
好处是路径边界、映射规则、判分规则都能不启服务直接单测。

## 接口一览（除标注的写入外全部只读）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | ``/health`` | 根目录存在性、能力矩阵、版本 |
| GET | ``/objects`` | 课程 + 自主学习对象、笔记关联状态、quiz-library 状态 |
| GET | ``/object`` | 单个对象详情（``?id=course:机器视觉``） |
| PUT | ``/object/notes`` | **写**：对象 → Obsidian 笔记目录映射（本地 plugin-data） |
| GET | ``/materials`` | 对象资料清单（一层；``?subdir=`` 展开一层） |
| GET | ``/materials/preview`` | 只读文本预览（``?path=`` 相对路径） |
| GET | ``/quiz/banks`` | quiz-library 题库列表（json 标准 / txt 既有） |
| GET | ``/quiz/bank`` | 单题库题目（``?bank=&type=&limit=``） |
| POST | ``/quiz/grade`` | 判分（与前端共用 ``quiz_schema.grade``） |
| POST | ``/quiz/drafts`` | **写**：agent 产出的题库 → 草稿 staging（不写用户目录） |
| GET | ``/quiz/drafts/{id}`` | 草稿详情 + 校验结果 |
| POST | ``/quiz/drafts/{id}/commit`` | **写**：用户确认后原子保存进 quiz-library |
| GET | ``/links`` | 对象 → Zotero collection / GitHub repo（只读） |
| PUT | ``/links`` | **写**：外链登记（github.com/<owner>/<repo> 与 8 位 key） |
| GET | ``/zotero/collections`` | Zotero collections（只读，离线如实报错） |
| GET | ``/github/repo`` | 仓库只读元数据（``?repo=owner/name``） |

## 安全边界（本文件必须保持）
* 客户端**从不**传绝对路径：对象用 ``course:`` / ``study:`` 前缀 id，材料用相对路径，
  两条都过 ``paths.safe_under``（resolve 后 ``is_relative_to`` 再判一次，链接不跟）。
* 唯一可写的用户目录是 ``<对象>/quiz-library/``，且必须走草稿 → 确认 → 原子替换；
  覆盖已有文件要显式 ``overwrite=true``。
* Zotero / GitHub 全部只读；GitHub 地址只接受 ``https://github.com/<owner>/<repo>``，
  拒绝其它主机 URL（不做 SSRF 跳板）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

# ── 内核包导入（在 Hermes 的插件隔离里也必须成立）──────────────────────────
#
# Hermes 用 ``plugin_isolation.import_user_code`` 把本文件当**顶层模块**执行
# （``spec_from_file_location("hermes_dashboard_plugin_wb-learn", plugin_api.py)``），
# 所以：
#   * 相对导入 ``from .wbl_core import …`` 不可用（无父包）；
#   * 裸 ``import wbl_core`` 依赖宿主 sys.path，而且真有一个同名的第三方包
#     （wbl-core / wbl_core）时会静默**加载错的那个** —— 这是最糟的失败方式。
#
# 因此这里按**文件位置**显式注册内核包，名字带插件前缀，不碰 sys.path、
# 也不可能与宿主或其它插件的模块撞名。``_wbl_core__<sub>`` 的命名同时让
# ``from __future__ import annotations`` 下的字符串注解仍能按模块名解析。
_PLUGIN_ID = "wb_learn_volume"
_CORE_DIR = Path(__file__).resolve().parent / "wbl_core"


def _load_core_package():
    """把同目录的 ``wbl_core/`` 注册成 ``wb_learn_volume_core`` 并返回它。"""
    import importlib.util
    import types

    pkg_name = f"{_PLUGIN_ID}_core"
    existing = sys.modules.get(pkg_name)
    if existing is not None and getattr(existing, "__wbl_core_dir__", None) == str(_CORE_DIR):
        return existing

    if not _CORE_DIR.is_dir():
        raise RuntimeError(f"wb-learn 内核目录缺失：{_CORE_DIR}")

    # 中间命名空间包：让 wbl_core 内部既可以用 ``from . import paths``（相对），
    # 也可以被这里以 ``wb_learn_volume_core.paths`` 取到。
    ns_name = _PLUGIN_ID
    if ns_name not in sys.modules:
        ns = types.ModuleType(ns_name)
        ns.__path__ = [str(_CORE_DIR.parent)]
        sys.modules[ns_name] = ns

    spec = importlib.util.spec_from_file_location(
        pkg_name, _CORE_DIR / "__init__.py",
        submodule_search_locations=[str(_CORE_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法为内核包建立模块 spec：{_CORE_DIR}")
    pkg = importlib.util.module_from_spec(spec)
    pkg.__wbl_core_dir__ = str(_CORE_DIR)
    sys.modules[pkg_name] = pkg
    spec.loader.exec_module(pkg)

    # wbl_core 内部用的是 ``from . import paths``，那些子模块会被注册成
    # ``wb_learn_volume_core.paths``；这里顺手挂到包对象上便于取用。
    return pkg


_core = _load_core_package()
VOLUME_VERSION = _core.VOLUME_VERSION
catalog = _core.catalog
links = _core.links
materials = _core.materials
paths = _core.paths
schema = _core.quiz_schema
quiz_store = _core.quiz_store

router = APIRouter()

MAX_DRAFT_BYTES = 4 * 1024 * 1024


def _bad(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _guard(fn, *args, **kwargs):
    """把内核异常翻译成 HTTP 语义，避免 500 里泄漏栈。"""
    try:
        return fn(*args, **kwargs)
    except paths.PathNotAllowed as exc:
        raise _bad(str(exc), 403) from exc
    except FileNotFoundError as exc:
        raise _bad(str(exc), 404) from exc
    except FileExistsError as exc:
        raise _bad(str(exc), 409) from exc
    except schema.SchemaError as exc:
        raise HTTPException(status_code=422, detail={"message": "题库结构非法", "issues": exc.issues}) from exc
    except ValueError as exc:
        raise _bad(str(exc), 422) from exc


def _object_id(value: Optional[str]) -> str:
    if not value or len(value) > 140:
        raise _bad("缺少 id（形如 course:机器视觉 / study:RL教学）")
    value = value.strip()
    if ":" not in value:
        raise _bad("id 必须带 course: 或 study: 前缀")
    return value


# --------------------------------------------------------------------------
# 概览
# --------------------------------------------------------------------------
@router.get("/health")
def health() -> Dict[str, Any]:
    data = _guard(catalog.list_objects)
    return {
        "ok": True,
        "plugin": "wb-learn",
        "version": VOLUME_VERSION,
        "roots": data["roots"],
        "objects": data["count"],
        "mapping_file": data["mapping_file"],
        "capabilities": materials.capabilities(),
        "writable": [str(catalog.store_dir()), "<对象目录>/quiz-library"],
        "read_only_external": ["Zotero (127.0.0.1:23119)", "GitHub (GET only)"],
    }


@router.get("/objects")
def objects() -> Dict[str, Any]:
    return _guard(catalog.list_objects)


@router.get("/object")
def object_detail(id: str = Query(..., max_length=140)) -> Dict[str, Any]:
    object_id = _object_id(id)
    obj = _guard(catalog.get_object, object_id)
    if obj is None:
        raise _bad(f"未知学习对象：{object_id}", 404)
    return {"object": obj, "capabilities": materials.capabilities()}


@router.put("/object/notes")
def set_notes(id: str = Query(..., max_length=140),
              vault: Optional[str] = Query(None, max_length=120)) -> Dict[str, Any]:
    """显式设置（或清除）对象 → 笔记目录映射。只写 plugin-data。"""
    return _guard(catalog.set_note_mapping, _object_id(id), (vault or "").strip() or None)


# --------------------------------------------------------------------------
# 资料
# --------------------------------------------------------------------------
@router.get("/materials")
def material_list(id: str = Query(..., max_length=140),
                  subdir: str = "") -> Dict[str, Any]:
    # subdir 用普通默认值而不是 Query(...)：这样不启服务也能直接单测
    if len(subdir) > 300:
        raise _bad("subdir 太长")
    return _guard(materials.list_materials, _object_id(id), subdir)


@router.get("/materials/preview")
def material_preview(id: str = Query(..., max_length=140),
                     path: str = Query(..., max_length=400)) -> Dict[str, Any]:
    return _guard(materials.preview, _object_id(id), path)


@router.get("/notes/files")
def note_files(id: str = Query(..., max_length=140)) -> Dict[str, Any]:
    """已关联 Obsidian 笔记目录里的 .md 清单（路径由服务端从映射推导）。"""
    return _guard(materials.list_note_files, _object_id(id))


@router.get("/notes/preview")
def note_preview(id: str = Query(..., max_length=140),
                 path: str = Query(..., max_length=400)) -> Dict[str, Any]:
    """预览一篇笔记；跨课程读取会被拒绝（只允许当前对象关联的目录）。"""
    return _guard(materials.preview_note, _object_id(id), path)


# --------------------------------------------------------------------------
# 题库
# --------------------------------------------------------------------------
@router.get("/quiz/banks")
def quiz_banks(id: str = Query(..., max_length=140)) -> Dict[str, Any]:
    return _guard(quiz_store.list_banks, _object_id(id))


@router.get("/quiz/bank")
def quiz_bank(id: str = Query(..., max_length=140),
              bank: str = Query(..., max_length=200),
              type: Optional[str] = None,
              limit: int = 300) -> Dict[str, Any]:
    return _guard(quiz_store.load_bank, _object_id(id), bank, limit=limit, qtype=type)


@router.post("/quiz/grade")
def quiz_grade(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """判分：``{question, chosen}`` 单题，或 ``{questions:[...], answers:{qid:[...]}}`` 批量。

    后端不保存任何作答（进度留在前端），这里只是把判分规则收敛到一处。
    """
    if not isinstance(payload, dict):
        raise _bad("请求体必须是对象")
    question = payload.get("question")
    if isinstance(question, dict):
        q = _guard(schema.normalize_question, question, 1)
        result = schema.grade(q, payload.get("chosen"))
        return {"results": [{"id": q["id"], **result}]}

    questions = payload.get("questions")
    answers = payload.get("answers") or {}
    if not isinstance(questions, list) or not isinstance(answers, dict):
        raise _bad("需要 questions 数组与 answers 映射")
    if len(questions) > 500:
        raise _bad("单次判分最多 500 题")
    results: List[Dict[str, Any]] = []
    for index, raw in enumerate(questions, start=1):
        q = _guard(schema.normalize_question, raw, index)
        result = schema.grade(q, answers.get(q["id"]))
        results.append({"id": q["id"], "type": q["type"], **result})
    judged = [r for r in results if r["judged"]]
    return {
        "results": results,
        "judged": len(judged),
        "correct": sum(1 for r in judged if r["correct"]),
        "manual": [r["id"] for r in results if not r["judged"]],
    }


# --------------------------------------------------------------------------
# 题库生成：草稿 → 预览校验 → 确认保存
# --------------------------------------------------------------------------
@router.post("/quiz/drafts")
def quiz_stage_draft(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """把 agent 产出的题库 JSON 落成草稿。**不写 quiz-library**。

    请求体：``{object_id, title?, bank: {schema_version?, questions: [...]}}``；
    也接受把 ``questions`` 直接放在顶层。
    """
    if not isinstance(payload, dict):
        raise _bad("请求体必须是对象")
    object_id = _object_id(payload.get("object_id"))
    bank = payload.get("bank") if isinstance(payload.get("bank"), dict) else payload
    if "questions" not in bank:
        raise _bad("缺少 questions 数组")
    if len(json.dumps(bank, ensure_ascii=False).encode("utf-8")) > MAX_DRAFT_BYTES:
        raise _bad("草稿超过 4MB 上限")
    title = payload.get("title")
    return _guard(quiz_store.stage_draft, object_id, bank,
                  title=(title if isinstance(title, str) else None))


@router.get("/quiz/drafts/{draft_id}")
def quiz_get_draft(draft_id: str) -> Dict[str, Any]:
    record = _guard(quiz_store.get_draft, draft_id)
    return {k: v for k, v in record.items() if k != "questions"} | {
        "question_count": len(record.get("questions") or []),
        "questions": (record.get("questions") or [])[:60],
    }


@router.post("/quiz/drafts/{draft_id}/commit")
def quiz_commit_draft(draft_id: str, payload: Optional[Dict[str, Any]] = Body(None)) -> Dict[str, Any]:
    payload = payload or {}
    object_id = _object_id(payload.get("object_id"))
    filename = payload.get("filename")
    if filename is not None and not isinstance(filename, str):
        raise _bad("filename 必须是字符串")
    return _guard(quiz_store.commit_draft, object_id, draft_id,
                  filename=filename, overwrite=bool(payload.get("overwrite")))


# --------------------------------------------------------------------------
# 只读外链
# --------------------------------------------------------------------------
@router.get("/links")
def get_links(id: str = Query(..., max_length=140)) -> Dict[str, Any]:
    return _guard(links.object_links, _object_id(id))


@router.put("/links")
def put_links(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """登记对象外链。``zotero`` 收 8 位 key，``github`` 收 github.com 地址；缺省字段不改。"""
    if not isinstance(payload, dict):
        raise _bad("请求体必须是对象")
    object_id = _object_id(payload.get("id"))
    kwargs: Dict[str, Any] = {}
    if "zotero" in payload:
        kwargs["zotero"] = payload.get("zotero")
    if "github" in payload:
        kwargs["github"] = payload.get("github")
    if not kwargs:
        raise _bad("至少给 zotero 或 github（传 null 表示清除）")
    _guard(links.save_object_links, object_id, **kwargs)
    return {"ok": True, "links": _guard(links.object_links, object_id)}


@router.get("/zotero/collections")
def zotero_collections() -> Dict[str, Any]:
    return links.zotero_collections()


@router.get("/zotero/collection")
def zotero_collection(key: str = Query(..., min_length=8, max_length=8),
                      limit: int = 30) -> Dict[str, Any]:
    return _guard(links.zotero_collection_items, key.upper(), limit=limit)


@router.get("/github/repo")
def github_repo(repo: str = Query(..., max_length=200)) -> Dict[str, Any]:
    parsed = links.parse_github_url(repo if repo.startswith("http") else f"https://github.com/{repo}")
    if parsed is None:
        raise _bad("只接受 github.com/<owner>/<repo>（不允许其它主机或带 query 的 URL）")
    return _guard(links.github_repo, parsed[0], parsed[1])
