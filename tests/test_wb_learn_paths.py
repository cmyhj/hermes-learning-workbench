"""wb-learn 路径边界：注册根、链接逃逸、``..`` 穿越、隐藏目录。

全部用 scratch 伪课程，**不读不写**真实 ``E:/Libraries/Courses`` / ``E:/Study``。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wb_learn_support import SAMPLE_BANK, TEXT_BANK, make_symlink_or_skip, scratch  # noqa: F401


def _api(scratch):
    return scratch.api()


# ---------------------------------------------------------------- 对象发现
def test_objects_are_discovered_one_level_only(scratch):
    scratch.course("课程A", {"第1章.md": "# A"})
    scratch.course("课程B", {"sub/第1章.md": "# B"})       # 子目录里的文件不算对象
    scratch.study_topic("主题A", {"NOTES.md": "note"})
    api = _api(scratch)

    data = api.objects()
    ids = [o["id"] for o in data["objects"]]
    assert "course:课程A" in ids
    assert "course:课程B" in ids
    assert "course:sub" not in ids, "二级目录不能被当成课程"
    assert "study:主题A" in ids
    assert data["roots"]["courses"]["exists"] is True
    assert data["count"] == len(ids)


def test_missing_roots_are_reported_not_faked(scratch, tmp_path):
    scratch.course("课程A")
    api = _api(scratch)
    # 把 study 根指到一个不存在的位置
    api.catalog._roots = lambda: {"courses": scratch.courses, "vault": scratch.vault,
                                  "study": tmp_path / "nope"}
    data = api.objects()
    assert data["roots"]["study"]["exists"] is False
    assert any("study 根目录不存在" in e for e in data["errors"]), data["errors"]


def test_vault_is_not_a_course(scratch):
    scratch.course("课程A")
    api = _api(scratch)
    ids = [o["id"] for o in api.objects()["objects"]]
    assert "course:vault" not in ids


def test_hidden_and_skipped_dirs_are_ignored(scratch):
    scratch.course("课程A")
    (scratch.courses / ".hidden-course").mkdir()
    (scratch.courses / "node_modules").mkdir()
    (scratch.courses / "课程A" / "__pycache__").mkdir()
    api = _api(scratch)
    ids = [o["id"] for o in api.objects()["objects"]]
    assert "course:.hidden-course" not in ids
    assert "course:node_modules" not in ids


def test_unknown_object_prefix_is_rejected(scratch):
    scratch.course("课程A")
    api = _api(scratch)
    from wbl_core import paths as P
    for bad in ("", "课程A", "file:课程A", "course:", ":课程A"):
        with pytest.raises((P.PathNotAllowed, ValueError)):
            api.catalog.object_dir(bad)


# ---------------------------------------------------------------- 符号链接逃逸
def test_symlinked_course_escaping_root_is_not_listed(scratch, tmp_path):
    outside = tmp_path / "outside-course"
    outside.mkdir()
    (outside / "secret.md").write_text("不该被看到", encoding="utf-8")
    make_symlink_or_skip(outside, scratch.courses / "逃逸课程")
    scratch.course("正常课程")
    api = _api(scratch)
    ids = [o["id"] for o in api.objects()["objects"]]
    assert "course:逃逸课程" not in ids, "指向根外的符号链接不能被当成课程"
    assert "course:正常课程" in ids


def test_file_reached_through_a_linked_parent_is_refused(scratch, tmp_path):
    """文件链接造不出来（Windows 要提权），但**通过链接目录**访问文件同样要挡。

    junction 只能指向目录，所以这里造 `课程A/外链 -> <根外目录>`，
    再请求 `外链/secret.md` —— 路径解析后落在根外，必须被拒。
    """
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "secret.md").write_text("根外文件", encoding="utf-8")
    d = scratch.course("课程A", {"ok.md": "ok"})
    make_symlink_or_skip(outside, d / "外链")

    api = _api(scratch)
    from wbl_core import paths as P

    # 列目录时链接不跟（不出现在清单里）
    names = [i["name"] for i in api.material_list(id="course:课程A")["items"]]
    assert "外链" not in names

    with pytest.raises(P.PathNotAllowed):
        api.materials.preview("course:课程A", "外链/secret.md")
    # 子目录列表同样被拒（路由层翻译成 403）
    with pytest.raises(api.HTTPException) as ei:
        api.material_list(id="course:课程A", subdir="外链")
    assert ei.value.status_code == 403


def test_symlinked_quiz_library_is_refused(scratch, tmp_path):
    outside = tmp_path / "outside-lib"
    outside.mkdir()
    d = scratch.course("课程A")
    make_symlink_or_skip(outside, d / "quiz-library")
    api = _api(scratch)
    listed = api.quiz_store.list_banks("course:课程A")
    assert listed["count"] == 0
    assert any("链接" in e or "不在对象目录内" in e for e in listed["errors"]), listed["errors"]


# ---------------------------------------------------------------- 相对路径穿越
@pytest.mark.parametrize("evil", [
    "../secret.md", "../../etc/passwd", "sub/../../x.md", "..\\x.md",
    "C:/Windows/win.ini", "/etc/passwd", "sub//x.md", ".hidden/x.md",
])
def test_relative_path_traversal_is_refused(scratch, evil):
    scratch.course("课程A", {"sub/ok.md": "ok"})
    api = _api(scratch)
    from wbl_core import paths as P
    with pytest.raises(P.PathNotAllowed):
        api.materials.preview("course:课程A", evil)


def test_absolute_path_is_never_accepted_as_object(scratch):
    scratch.course("课程A")
    api = _api(scratch)
    from wbl_core import paths as P
    for bad in ("course:C:/Windows", "course:../../etc", "study:/etc"):
        with pytest.raises(P.PathNotAllowed):
            api.catalog.object_dir(bad)


def test_subdir_depth_limit(scratch):
    scratch.course("课程A", {"a/b/c/d/e/f.md": "deep"})
    api = _api(scratch)
    from wbl_core import paths as P
    with pytest.raises(P.PathNotAllowed):
        api.materials.list_materials("course:课程A", "a/b/c/d")


def test_materials_listing_is_one_level(scratch):
    scratch.course("课程A", {"第1章.md": "x", "sub/深层.md": "y"})
    api = _api(scratch)
    listing = api.materials.list_materials("course:课程A")
    names = [i["name"] for i in listing["items"]]
    assert "第1章.md" in names and "sub" in names
    assert "深层.md" not in names, "一层列表不能递归"


# ---------------------------------------------------------------- 扫描边界
def test_quiz_library_is_not_enumerated_as_material(scratch):
    d = scratch.course("课程A", {"第1章.md": "x"})
    (d / "quiz-library").mkdir()
    (d / "quiz-library" / "bank.json").write_text("{}", encoding="utf-8")
    api = _api(scratch)
    names = [i["name"] for i in api.materials.list_materials("course:课程A")["items"]]
    assert "quiz-library" not in names


def test_no_recursive_walk_of_real_disk(scratch, monkeypatch):
    """确认扫描只用一层 iterdir：把深层目录塞满也不该出现在结果里。"""
    d = scratch.course("课程A")
    deep = d / "a" / "b" / "c"
    deep.mkdir(parents=True)
    for i in range(20):
        (deep / f"f{i}.md").write_text("x", encoding="utf-8")
    api = _api(scratch)
    listing = api.materials.list_materials("course:课程A")
    assert listing["count"] == 1          # 只有 a/
    assert listing["items"][0]["name"] == "a"
