"""只读资料清单 + 安全文本预览（格式能力如实声明）。

## 不做的事
* 不写任何资料文件（本模块没有写函数）；
* 不递归全盘：材料树按「一层 + 按需展开一个子目录」列举，深度有上限；
* 不盲装依赖：PDF 文本提取只在 ``fitz``（PyMuPDF）**已安装**时启用，
  未安装就返回 ``supported=False`` + 明确提示，不尝试 pip install。

## 格式支持矩阵（``capabilities()`` 会如实返回当前进程的真实能力）
| 格式 | 列表 | 文本预览 |
|---|---|---|
| .md / .txt / 代码 / .json | ✓ | ✓ 直接读（有字节上限） |
| .pdf | ✓ | 有 fitz 时抽文字层；扫描件无文字层时明确说「无文字层」 |
| .docx / .pptx / .xlsx | ✓ | 有 python-docx / python-pptx / openpyxl 时抽文字 |
| 图片 / 音视频 | ✓ | ✗（只给尺寸/大小，不做 OCR） |
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import catalog, paths

MAX_LIST_ITEMS = 400
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_CHARS = 200_000
MAX_PDF_PAGES = 40
MAX_DEPTH = 3

_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def capabilities() -> Dict[str, Any]:
    """当前进程真实的解析能力（不撒谎：缺什么就报 supported=False）。"""
    fitz = _has("fitz")
    docx = _has("docx")
    pptx = _has("pptx")
    openpyxl = _has("openpyxl")
    return {
        "text": {"list": True, "preview": True, "suffixes": sorted(paths.TEXT_SUFFIXES | paths.NOTE_SUFFIXES)},
        "pdf": {"list": True, "preview": bool(fitz), "supported": bool(fitz),
                "note": None if fitz else "未安装 PyMuPDF(fitz)：PDF 只列清单，不提供文本预览（不会自动安装）",
                "scanned_pdf": "扫描件/图片型 PDF 无文字层，抽不到文字时如实提示，不做 OCR"},
        "docx": {"list": True, "preview": bool(docx), "supported": bool(docx),
                 "note": None if docx else "未安装 python-docx：.docx 只列清单"},
        "pptx": {"list": True, "preview": bool(pptx), "supported": bool(pptx),
                 "note": None if pptx else "未安装 python-pptx：.pptx 只列清单"},
        "xlsx": {"list": True, "preview": bool(openpyxl), "supported": bool(openpyxl),
                 "note": None if openpyxl else "未安装 openpyxl：.xlsx 只列清单"},
        "image": {"list": True, "preview": False, "note": "图片不提供文本预览（不做 OCR）"},
        "media": {"list": True, "preview": False, "note": "音视频只列清单"},
    }


def _entry(path: Path, root: Path) -> Dict[str, Any]:
    kind = paths.classify_suffix(path)
    return {
        "name": path.name,
        "rel_path": paths.rel_display(root, path),
        "path": str(path),
        "kind": kind,
        "suffix": path.suffix.lower(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "is_dir": path.is_dir(),
    }


def list_materials(object_id: str, subdir: str = "") -> Dict[str, Any]:
    """列对象材料树。``subdir`` 只能是对象目录内的相对 posix 路径。"""
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    root = catalog.object_dir(object_id)
    base = _resolve_subdir(root, subdir)

    items = []
    for child in paths.iter_children(base)[:MAX_LIST_ITEMS]:
        items.append(_entry(child, root))
    items.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {
        "object": {k: obj[k] for k in ("id", "kind", "name", "path")},
        "subdir": paths.rel_display(root, base) if base != root else "",
        "count": len(items),
        "items": items,
        "notes": obj["notes"],
        "capabilities": capabilities(),
    }


def _resolve_subdir(root: Path, subdir: str) -> Path:
    subdir = (subdir or "").strip().strip("/")
    if not subdir:
        real = paths.safe_under(root, root, allow_root=True)
        if real is None:
            raise paths.PathNotAllowed("对象目录不可解析")
        return real
    parts = [p for p in subdir.split("/") if p]
    if len(parts) > MAX_DEPTH:
        raise paths.PathNotAllowed(f"子目录层级超过 {MAX_DEPTH}")
    for part in parts:
        if part in ("", ".", "..") or part.startswith(".") or ":" in part or "\\" in part:
            raise paths.PathNotAllowed(f"子目录片段非法：{part!r}")
    target = root.joinpath(*parts)
    real = paths.safe_under(root, target)
    if real is None or not real.is_dir():
        raise paths.PathNotAllowed("子目录不存在或解析后越界")
    return real


def _resolve_file(root: Path, rel_path: str) -> Path:
    rel_path = (rel_path or "").strip().strip("/")
    if not rel_path or len(rel_path) > 400:
        raise paths.PathNotAllowed("文件路径非法")
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
    if any(p in (".", "..") or p.startswith(".") or ":" in p for p in parts):
        raise paths.PathNotAllowed("文件路径片段非法")
    target = root.joinpath(*parts)
    real = paths.safe_under(root, target)
    if real is None or not real.is_file():
        raise paths.PathNotAllowed("文件不存在或解析后越界")
    return real


def _decode(raw: bytes) -> tuple[str, str]:
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _pdf_text(path: Path) -> Dict[str, Any]:
    import fitz  # noqa: PLC0415  —— 只有已安装时才走到这里

    page_count = 0
    chunks: List[str] = []
    with fitz.open(str(path)) as doc:
        page_count = doc.page_count
        pages = min(page_count, MAX_PDF_PAGES)
        for index in range(pages):
            text = doc[index].get_text("text")
            if text.strip():
                chunks.append(f"\n--- p.{index + 1} ---\n{text}")
    text = "".join(chunks)
    return {"text": text, "page_count": page_count,
            "pages_read": min(page_count, MAX_PDF_PAGES), "has_text_layer": bool(text.strip())}


def _docx_text(path: Path) -> str:
    import docx  # noqa: PLC0415

    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pptx_text(path: Path) -> str:
    from pptx import Presentation  # noqa: PLC0415

    out = []
    for index, slide in enumerate(Presentation(str(path)).slides, start=1):
        out.append(f"\n--- slide {index} ---")
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                out.append(shape.text)
    return "\n".join(out)


def _xlsx_text(path: Path) -> str:
    import openpyxl  # noqa: PLC0415

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    out = []
    for sheet in wb.worksheets[:5]:
        out.append(f"\n--- sheet {sheet.title} ---")
        for row in sheet.iter_rows(max_row=200, values_only=True):
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                out.append(" | ".join(cells))
    wb.close()
    return "\n".join(out)


def _text_kind(path: Path) -> str:
    if path.suffix.lower() in (".html", ".htm"):
        return "html"
    if path.suffix.lower() == ".md" or path.suffix.lower() == ".markdown":
        return "markdown"
    return "text"


_LINK_RE = re.compile(r"\b(?:https?|ftp)://[^\s)>\]]+", re.I)


def preview(object_id: str, rel_path: str) -> Dict[str, Any]:
    """安全文本预览：返回纯文本（html 会剥标签），不执行、不内联脚本。

    前端一律以文本方式渲染（``textContent``），不做 HTML 注入 —— 资料里带
    ``<script>`` 也只是文字。
    """
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    root = catalog.object_dir(object_id)
    path = _resolve_file(root, rel_path)
    result = _preview_path(root, path)
    result["object_id"] = object_id
    return result


def _preview_path(root: Path, path: Path) -> Dict[str, Any]:
    kind = paths.classify_suffix(path)
    size = path.stat().st_size
    caps = capabilities()
    result: Dict[str, Any] = {
        "object_id": None,
        "rel_path": paths.rel_display(root, path),
        "name": path.name,
        "kind": kind,
        "bytes": size,
        "suffix": path.suffix.lower(),
        "truncated": False,
        "text": "",
        "encoding": None,
        "mode": None,
        "supported": True,
        "notice": None,
    }

    if kind in ("image", "media"):
        result.update({"supported": False, "mode": "metadata",
                       "notice": f"{kind} 不提供文本预览（不做 OCR/转码）"})
        return result

    if kind == "text" or kind == "note":
        if size > MAX_PREVIEW_BYTES:
            result["truncated"] = True
        raw = path.open("rb").read(MAX_PREVIEW_BYTES)
        text, enc = _decode(raw)
        if path.suffix.lower() in (".html", ".htm"):
            text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
            text = re.sub(r"(?s)<[^>]+>", "", text)
            text = html.unescape(text)
            result["mode"] = "html-stripped"
        else:
            result["mode"] = _text_kind(path)
        result.update({"text": text[:MAX_PREVIEW_CHARS], "encoding": enc})
        if result["truncated"]:
            result["notice"] = f"文件超过 {MAX_PREVIEW_BYTES // 1024}KB，只预览前 {MAX_PREVIEW_BYTES // 1024}KB"
        return result

    if path.suffix.lower() == ".pdf":
        if not caps["pdf"]["supported"]:
            result.update({"supported": False, "mode": "metadata", "notice": caps["pdf"]["note"]})
            return result
        try:
            data = _pdf_text(path)
        except Exception as exc:  # 损坏/加密 PDF
            result.update({"supported": False, "mode": "error",
                           "notice": f"PDF 解析失败：{type(exc).__name__}: {exc}"})
            return result
        result.update({"text": data["text"][:MAX_PREVIEW_CHARS], "mode": "pdf-text-layer",
                       "pages": data["pages_read"], "page_count": data["page_count"]})
        if not data["has_text_layer"]:
            result.update({"supported": False,
                           "notice": "这个 PDF 没有文字层（扫描件/图片型）：不支持文本提取，未做 OCR"})
        return result

    extractors = {".docx": ("docx", _docx_text), ".pptx": ("pptx", _pptx_text), ".xlsx": ("xlsx", _xlsx_text)}
    if path.suffix.lower() in extractors:
        key, fn = extractors[path.suffix.lower()]
        if not caps[key]["supported"]:
            result.update({"supported": False, "mode": "metadata", "notice": caps[key]["note"]})
            return result
        try:
            text = fn(path)
        except Exception as exc:
            result.update({"supported": False, "mode": "error",
                           "notice": f"解析失败：{type(exc).__name__}: {exc}"})
            return result
        result.update({"text": text[:MAX_PREVIEW_CHARS], "mode": f"{key}-text",
                       "truncated": len(text) > MAX_PREVIEW_CHARS})
        return result

    result.update({"supported": False, "mode": "metadata",
                   "notice": f"不支持预览的格式：{path.suffix or '（无扩展名）'}"})
    return result


def list_note_files(object_id: str, *, limit: int = MAX_LIST_ITEMS) -> Dict[str, Any]:
    """列出**已关联**的 Obsidian 笔记目录里的 .md 文件（多层，深度受限）。

    笔记目录不在对象目录下（在 ``vault/01-Courses/<课程>``），所以单独一个入口：
    路径由服务端从映射推导，客户端永远不传路径。未关联时返回 ``linked=False``。
    """
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    notes = obj["notes"]
    if not notes.get("linked") or not notes.get("vault_path"):
        return {"object_id": object_id, "linked": False, "reason": notes.get("reason"),
                "count": 0, "files": [], "root": None}
    vault_root = paths.default_roots()["vault"]
    base = paths.safe_under(vault_root, Path(notes["vault_path"]))
    if base is None or not base.is_dir():
        return {"object_id": object_id, "linked": False, "reason": "笔记目录不可解析",
                "count": 0, "files": [], "root": None}

    files: List[Dict[str, Any]] = []
    stack: List[tuple] = [(base, 0)]
    while stack and len(files) < limit:
        current, depth = stack.pop()
        for child in paths.iter_children(current):
            if child.is_dir():
                if depth + 1 <= MAX_DEPTH:
                    stack.append((child, depth + 1))
                continue
            if child.suffix.lower() not in paths.NOTE_SUFFIXES:
                continue
            files.append({
                "name": child.name,
                "rel_path": paths.rel_display(vault_root, child),
                "bytes": child.stat().st_size,
            })
            if len(files) >= limit:
                break
    files.sort(key=lambda f: f["rel_path"].lower())
    return {"object_id": object_id, "linked": True, "root": str(base),
            "count": len(files), "files": files}


def preview_note(object_id: str, rel_path: str) -> Dict[str, Any]:
    """预览一篇已关联笔记。``rel_path`` 必须落在 vault 根之内。"""
    obj = catalog.get_object(object_id)
    if obj is None:
        raise paths.PathNotAllowed("未知学习对象 id")
    notes = obj.get("notes") or {}
    if not notes.get("linked"):
        raise paths.PathNotAllowed("该对象还没有关联笔记目录")
    vault_root = paths.default_roots()["vault"]
    note_dir = paths.safe_under(vault_root, Path(notes["vault_path"]))
    if note_dir is None:
        raise paths.PathNotAllowed("笔记目录不可解析")
    target = _resolve_file(vault_root, rel_path)
    # 再确认笔记文件确实在**这个对象关联的**笔记目录内（防止跨课程读）
    if paths.safe_under(note_dir, target) is None:
        raise paths.PathNotAllowed("笔记不属于当前对象关联的目录")
    result = preview_via(vault_root, target)
    result["object_id"] = object_id
    return result


def preview_via(root: Path, path: Path) -> Dict[str, Any]:
    """``preview`` 的内部实现：``root`` 只用于生成相对路径。"""
    return _preview_path(root, path)


def material_index(object_id: str, *, limit: int = 60) -> List[Dict[str, Any]]:
    obj = catalog.get_object(object_id)
    if obj is None:
        return []
    root = catalog.object_dir(object_id)
    out: List[Dict[str, Any]] = []
    for child in paths.iter_children(root):
        if child.is_dir():
            for sub in paths.iter_children(child):
                if sub.is_file():
                    out.append({"path": str(sub), "rel_path": paths.rel_display(root, sub),
                                "kind": paths.classify_suffix(sub)})
                if len(out) >= limit:
                    return out
        elif child.is_file():
            out.append({"path": str(child), "rel_path": paths.rel_display(root, child),
                        "kind": paths.classify_suffix(child)})
        if len(out) >= limit:
            break
    return out
