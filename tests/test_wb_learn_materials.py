"""wb-learn 资料预览：只读、安全文本、格式能力如实声明。

用 scratch 文件验证「支持什么/不支持什么」都说清楚，不做 OCR、不盲装依赖。
真实 PDF/笔记用例单独一组，只读真实小资料。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wb_learn_support import scratch  # noqa: F401


def test_text_preview_returns_content(scratch):
    scratch.course("课程A", {"第1章.md": "# 标题\n\n正文内容。"})
    api = scratch.api()
    p = api.material_preview(id="course:课程A", path="第1章.md")
    assert p["supported"] is True
    assert p["mode"] == "markdown"
    assert "正文内容" in p["text"]
    assert p["rel_path"] == "第1章.md"


def test_html_is_stripped_not_executed(scratch):
    scratch.course("课程A", {"page.html": "<h1>标题</h1><script>alert(1)</script><p>正文</p>"})
    api = scratch.api()
    p = api.material_preview(id="course:课程A", path="page.html")
    assert p["mode"] == "html-stripped"
    assert "<script" not in p["text"]
    assert "alert" not in p["text"], "脚本内容必须整块剥掉"
    assert "标题" in p["text"] and "正文" in p["text"]


def test_unsupported_format_says_so(scratch):
    scratch.course("课程A", {"未知.xyz": "binary-ish"})
    api = scratch.api()
    p = api.material_preview(id="course:课程A", path="未知.xyz")
    assert p["supported"] is False
    assert p["mode"] == "metadata"
    assert "不支持" in p["notice"]


def test_image_is_listed_but_not_extracted(scratch):
    scratch.course("课程A", {"图.png": "not-a-real-png"})
    api = scratch.api()
    listing = api.material_list(id="course:课程A")
    kinds = {i["name"]: i["kind"] for i in listing["items"]}
    assert kinds["图.png"] == "image"
    p = api.material_preview(id="course:课程A", path="图.png")
    assert p["supported"] is False
    assert "OCR" in p["notice"]


def test_big_text_is_truncated_and_flagged(scratch):
    scratch.course("课程A", {"大.txt": "行内容\n" * 60000})
    api = scratch.api()
    p = api.material_preview(id="course:课程A", path="大.txt")
    assert p["truncated"] is True
    assert p["notice"] and "KB" in p["notice"]
    assert len(p["text"]) <= 200_000


def test_capabilities_report_actual_modules(scratch):
    scratch.course("课程A")
    api = scratch.api()
    caps = api.material_list(id="course:课程A")["capabilities"]
    for key in ("text", "pdf", "docx", "pptx", "xlsx", "image", "media"):
        assert key in caps
    assert caps["text"]["preview"] is True
    assert caps["image"]["preview"] is False
    # PDF 能力必须与真实安装状态一致（不是写死 True）
    try:
        import fitz  # noqa: F401
        assert caps["pdf"]["supported"] is True
        assert caps["pdf"]["note"] is None
    except ImportError:
        assert caps["pdf"]["supported"] is False
        assert caps["pdf"]["note"]


def test_missing_file_is_403_not_500(scratch):
    scratch.course("课程A")
    api = scratch.api()
    with pytest.raises(api.HTTPException) as ei:
        api.material_preview(id="course:课程A", path="没有这个文件.md")
    assert ei.value.status_code == 403


def test_directory_cannot_be_previewed_as_file(scratch):
    scratch.course("课程A", {"sub/x.md": "x"})
    api = scratch.api()
    with pytest.raises(api.HTTPException) as ei:
        api.material_preview(id="course:课程A", path="sub")
    assert ei.value.status_code == 403


def test_listing_includes_notes_state_and_quiz_dir_flag(scratch):
    scratch.course("课程A", {"第1章.md": "x"})
    scratch.vault_course("课程A", {"00.md": "x"})
    api = scratch.api()
    listing = api.material_list(id="course:课程A")
    assert listing["notes"]["linked"] is True
    assert listing["notes"]["kind"] == "exact"


# ---------------------------------------------------------------- 真实小资料（只读）
REAL_COURSE = Path("E:/Libraries/Courses/机器视觉")
real_data = pytest.mark.skipif(
    not (REAL_COURSE / "2026-10_机器视觉实验" / "机器视觉课程复习题库.txt").is_file(),
    reason="真实机器视觉资料不存在",
)


@real_data
def test_real_text_material_preview_reads_only(monkeypatch):
    """读真实小资料，但只读：先记 mtime，读完必须一模一样。"""
    from wbl_core import materials as M

    monkeypatch.setenv("WB_LEARN_COURSES_ROOT", "E:/Libraries/Courses")
    monkeypatch.setenv("WB_LEARN_VAULT_ROOT", "E:/Libraries/Courses/vault/01-Courses")
    monkeypatch.setenv("WB_LEARN_STUDY_ROOT", "E:/Study")
    monkeypatch.setenv("WB_LEARN_DATA_DIR", str(Path.home() / ".wb-learn-readonly-probe"))

    rel = "2026-10_机器视觉实验/机器视觉课程复习题库.txt"
    target = REAL_COURSE / rel
    before = target.stat().st_mtime_ns

    p = M.preview("course:机器视觉", rel)
    assert p["supported"] is True
    assert "机器视觉" in p["text"]
    assert p["bytes"] > 1000

    assert target.stat().st_mtime_ns == before, "预览绝不能写真实资料"
    shutil_rm = Path.home() / ".wb-learn-readonly-probe"
    if shutil_rm.is_dir():
        import shutil
        shutil.rmtree(shutil_rm, ignore_errors=True)


@real_data
def test_real_notes_are_scoped_by_exact_name(monkeypatch):
    from wbl_core import catalog, materials as M

    monkeypatch.setenv("WB_LEARN_COURSES_ROOT", "E:/Libraries/Courses")
    monkeypatch.setenv("WB_LEARN_VAULT_ROOT", "E:/Libraries/Courses/vault/01-Courses")
    monkeypatch.setenv("WB_LEARN_STUDY_ROOT", "E:/Study")
    monkeypatch.setenv("WB_LEARN_DATA_DIR", str(Path.home() / ".wb-learn-readonly-probe2"))

    obj = catalog.get_object("course:机器视觉")
    assert obj is not None
    assert obj["notes"]["linked"] is True
    assert obj["notes"]["kind"] == "exact", "真实机器视觉在课程库与 vault 里同名，应自动关联"

    listing = M.list_note_files("course:机器视觉")
    assert listing["linked"] is True
    assert listing["count"] > 0
    assert all(f["rel_path"].startswith("机器视觉/") for f in listing["files"])

    import shutil
    shutil.rmtree(Path.home() / ".wb-learn-readonly-probe2", ignore_errors=True)
