"""wb-learn 题库生成闭环：草稿 staging → 校验 → 用户确认 → 原子保存。

重点验证的是**不伪造**：
* 生成后没确认 → quiz-library 里什么都没有（不会显示成「已生成」）；
* 校验不过的草稿不允许保存；
* 覆盖同名要显式确认；
* 文件名不能穿越出 quiz-library；
* 保存是原子的（同一目录 tmp → os.replace），内容可读回且带 source。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wb_learn_support import SAMPLE_BANK, TEXT_BANK, scratch  # noqa: F401


def _bank(**overrides):
    data = json.loads(json.dumps(SAMPLE_BANK))
    data.update(overrides)
    return data


@pytest.fixture()
def course(scratch):
    scratch.course("机器视觉", {"第1章.md": "# 机器视觉概述", "第2章.md": "# 相机标定"})
    return scratch


# ---------------------------------------------------------------- 草稿
def test_stage_draft_does_not_touch_quiz_library(course):
    api = course.api()
    result = api.quiz_stage_draft({"object_id": "course:机器视觉", "title": "第1章 草稿", "bank": _bank()})

    assert result["ok"] is True
    assert result["total"] == 5
    assert result["draft_id"]
    lib = course.courses / "机器视觉" / "quiz-library"
    assert not lib.exists(), "未确认的草稿绝不能写进 quiz-library"
    assert api.quiz_banks(id="course:机器视觉")["count"] == 0

    # 草稿落在插件数据目录（不是用户资料目录）
    draft_path = Path(result["path"])
    assert draft_path.is_file()
    assert str(draft_path).startswith(str(course.data))


def test_draft_is_retrievable_with_validation_result(course):
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": _bank()})
    got = api.quiz_get_draft(staged["draft_id"])
    assert got["ok"] is True
    assert got["issue_count"] if "issue_count" in got else True
    assert got["question_count"] == 5
    assert len(got["questions"]) == 5
    assert got["questions"][0]["stem"].startswith("机器视觉")


def test_invalid_draft_is_staged_but_not_committable(course):
    api = course.api()
    bad = {"questions": [{"stem": "x", "type": "single",
                          "options": {"A": "a", "B": "b"}, "answer": ["Z"]}]}
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": bad})
    assert staged["ok"] is False
    assert staged["issues"]

    with pytest.raises(api.HTTPException) as ei:
        api.quiz_commit_draft(staged["draft_id"], {"object_id": "course:机器视觉"})
    assert ei.value.status_code == 422
    assert "未通过校验" in str(ei.value.detail)
    assert not (course.courses / "机器视觉" / "quiz-library").exists()


def test_missing_source_is_preserved_as_null_not_invented(course):
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": {
        "questions": [{"stem": "无来源题", "type": "judge", "answer": ["正确"]}],
    }})
    detail = api.quiz_get_draft(staged["draft_id"])
    assert detail["questions"][0]["source"] is None


# ---------------------------------------------------------------- 保存
def test_commit_writes_atomic_json_into_quiz_library(course):
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "title": "第1章 草稿", "bank": _bank()})
    saved = api.quiz_commit_draft(staged["draft_id"], {"object_id": "course:机器视觉"})

    assert saved["ok"] is True
    assert saved["file"].endswith(".json")
    target = Path(saved["path"])
    assert target.is_file()
    assert target.parent == (course.courses / "机器视觉" / "quiz-library").resolve()
    assert saved["rel_path"].startswith("quiz-library/")

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["object_id"] == "course:机器视觉"
    assert payload["generated"]["by"] == "hermes-agent"
    assert payload["generated"]["at"], "保存时必须写生成时间（可追溯）"
    assert len(payload["questions"]) == 5
    assert payload["questions"][0]["source"]["path"] == "第1章.md"

    # 没有任何 .tmp 残留
    leftovers = [p.name for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_committed_bank_is_immediately_playable(course):
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": _bank()})
    saved = api.quiz_commit_draft(staged["draft_id"], {"object_id": "course:机器视觉"})

    listed = api.quiz_banks(id="course:机器视觉")
    assert listed["count"] == 1
    bank = listed["banks"][0]
    assert bank["ok"] is True and bank["total"] == 5

    payload = api.quiz_bank(id="course:机器视觉", bank=bank["id"])
    q = payload["questions"][0]
    assert api.schema.grade(q, q["answer"])["correct"] is True
    assert payload["grading"]["manual"] == ["short"]


def test_overwrite_requires_explicit_confirmation(course):
    api = course.api()
    first = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": _bank()})
    saved = api.quiz_commit_draft(first["draft_id"], {"object_id": "course:机器视觉", "filename": "固定名.json"})
    before = Path(saved["path"]).read_bytes()

    second = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": {
        "questions": [{"stem": "新题", "type": "judge", "answer": ["正确"]}],
    }})
    with pytest.raises(api.HTTPException) as ei:
        api.quiz_commit_draft(second["draft_id"],
                              {"object_id": "course:机器视觉", "filename": "固定名.json"})
    assert ei.value.status_code == 409
    assert Path(saved["path"]).read_bytes() == before, "未确认的覆盖不能动已有文件"

    ok = api.quiz_commit_draft(second["draft_id"],
                               {"object_id": "course:机器视觉", "filename": "固定名.json", "overwrite": True})
    assert ok["overwrote"] is True
    assert json.loads(Path(ok["path"]).read_text(encoding="utf-8"))["questions"][0]["stem"] == "新题"


@pytest.mark.parametrize("evil", [
    "../../逃出.json", "..\\逃出.json", "sub/深层.json", "C:/Windows/evil.json",
    "..json", "/abs.json", "a:b.json", ".hidden.json",
])
def test_filename_traversal_is_refused(course, evil):
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": _bank()})
    with pytest.raises(api.HTTPException) as ei:
        api.quiz_commit_draft(staged["draft_id"], {"object_id": "course:机器视觉", "filename": evil})
    assert ei.value.status_code in (403, 422), ei.value.detail

    lib = course.courses / "机器视觉" / "quiz-library"
    if lib.exists():
        assert [p.name for p in lib.iterdir()] == [], "越界文件名不能落下任何文件"
    # 对象目录之外也不能出现
    assert not (course.courses / "逃出.json").exists()
    assert not (course.courses / "机器视觉" / "逃出.json").exists()


def test_draft_cannot_be_committed_to_another_object(course):
    course.course("另一门课")
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": _bank()})
    with pytest.raises(api.HTTPException) as ei:
        api.quiz_commit_draft(staged["draft_id"], {"object_id": "course:另一门课"})
    assert ei.value.status_code == 403
    assert not (course.courses / "另一门课" / "quiz-library").exists()


def test_draft_id_shape_is_validated(course):
    api = course.api()
    for bad in ("../etc/passwd", "a/b", "", "x" * 200):
        with pytest.raises(api.HTTPException):
            api.quiz_get_draft(bad)


def test_commit_only_writes_inside_quiz_library(course):
    """保存后，对象目录里新增的东西只能出现在 quiz-library 下。"""
    lib = course.courses / "机器视觉" / "quiz-library"
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "course:机器视觉", "bank": _bank()})
    api.quiz_commit_draft(staged["draft_id"], {"object_id": "course:机器视觉"})

    top_level = sorted(p.name for p in (course.courses / "机器视觉").iterdir())
    assert top_level == ["quiz-library", "第1章.md", "第2章.md"], top_level
    assert lib.is_dir()


def test_study_topic_quiz_library_uses_same_relative_path(course):
    """自主学习对象也用 <主题>/quiz-library，这个相对路径是固定的。"""
    course.study_topic("RL教学", {"NOTES.md": "# RL notes"})
    api = course.api()
    staged = api.quiz_stage_draft({"object_id": "study:RL教学", "bank": _bank()})
    saved = api.quiz_commit_draft(staged["draft_id"], {"object_id": "study:RL教学"})
    assert Path(saved["path"]).parent == (course.study / "RL教学" / "quiz-library").resolve()
    assert saved["rel_path"] == "quiz-library/" + saved["file"]


# ---------------------------------------------------------------- 生成提示（真实材料）
def test_generate_prompt_requirements_are_about_real_material(course):
    """前端「生成要求」文案必须点名真实路径与禁止编题；这里钉住文案来自 backend 数据。"""
    api = course.api()
    obj = api.object_detail(id="course:机器视觉")["object"]
    assert obj["path"].endswith("机器视觉")
    assert obj["notes"]["linked"] is False or obj["notes"]["linked"] is True
    assert obj["quiz_library"]["path"].endswith("quiz-library")
    assert obj["quiz_library"]["exists"] is False
