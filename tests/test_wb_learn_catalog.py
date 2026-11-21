"""wb-learn 笔记关联与分类：精确同名自动关联、差异名称必须显式选择、映射持久化。

关键约束（都是本模块的产品要求，用例逐条钉住）：
* 名称有差异时**绝不**模糊自动匹配，只列候选等用户选；
* 用户选择持久化到 plugin-data，重启后仍是它；
* 映射指向已不存在的目录时如实报 stale，不静默回退同名匹配；
* 「关联到无笔记」也是一种选择，要持久化。
"""

from __future__ import annotations

import json

import pytest

from wb_learn_support import SAMPLE_BANK, TEXT_BANK, scratch  # noqa: F401


def test_exact_same_name_links_automatically(scratch):
    scratch.course("机器视觉", {"第1章.md": "x"})
    scratch.vault_course("机器视觉", {"00-总览.md": "# 总览"})
    api = scratch.api()

    obj = api.object_detail(id="course:机器视觉")["object"]
    notes = obj["notes"]
    assert notes["linked"] is True
    assert notes["kind"] == "exact"
    assert notes["vault"] == "机器视觉"
    assert notes["stale"] is False


def test_different_name_is_not_auto_matched(scratch):
    """机辅设计 vs 机械设计：名字相近也必须停在 unlinked。"""
    scratch.course("机器视觉", {"第1章.md": "x"})
    scratch.vault_course("机器视觉Ⅱ", {"00-总览.md": "x"})
    scratch.vault_course("机器视觉（新）", {"00-总览.md": "x"})
    api = scratch.api()

    notes = api.object_detail(id="course:机器视觉")["object"]["notes"]
    assert notes["linked"] is False, "名称差异不得模糊匹配"
    assert notes["kind"] == "unlinked"
    assert notes["stale"] is False
    assert "机器视觉Ⅱ" in notes["candidates"]
    assert "机器视觉（新）" in notes["candidates"]
    assert "请在 UI 显式选择" in notes["reason"]


def test_manual_selection_persists(scratch):
    scratch.course("机器视觉", {"第1章.md": "x"})
    scratch.vault_course("机器视觉笔记", {"00-总览.md": "x"})
    api = scratch.api()

    result = api.set_notes(id="course:机器视觉", vault="机器视觉笔记")
    assert result["ok"] is True
    assert result["object"]["notes"]["kind"] == "manual"
    assert result["object"]["notes"]["vault"] == "机器视觉笔记"

    # 落盘读回（模拟「下次打开」）
    mapping = scratch.read_mapping()
    assert mapping["course:机器视觉"]["vault"] == "机器视觉笔记"
    fresh = scratch.api().object_detail(id="course:机器视觉")["object"]
    assert fresh["notes"]["kind"] == "manual"
    assert fresh["notes"]["linked"] is True


def test_explicit_no_notes_is_persisted(scratch):
    scratch.course("机器视觉", {"第1章.md": "x"})
    scratch.vault_course("机器视觉", {"00-总览.md": "x"})   # 同名可用
    api = scratch.api()
    assert api.object_detail(id="course:机器视觉")["object"]["notes"]["kind"] == "exact"

    api.set_notes(id="course:机器视觉", vault=None)         # 用户显式排除
    notes = scratch.api().object_detail(id="course:机器视觉")["object"]["notes"]
    assert notes["linked"] is False
    assert notes["kind"] == "none"
    assert "显式选择" in notes["reason"]


def test_stale_mapping_is_reported_not_silently_rematched(scratch):
    scratch.course("机器视觉", {"第1章.md": "x"})
    v = scratch.vault_course("机器视觉", {"00-总览.md": "x"})
    api = scratch.api()
    api.set_notes(id="course:机器视觉", vault="机器视觉")
    assert scratch.api().object_detail(id="course:机器视觉")["object"]["notes"]["kind"] == "manual"

    for f in v.iterdir():
        f.unlink()
    v.rmdir()                                # 笔记目录被移走

    notes = scratch.api().object_detail(id="course:机器视觉")["object"]["notes"]
    assert notes["stale"] is True
    assert notes["linked"] is False
    assert "当前不存在" in notes["reason"]


def test_selecting_unknown_vault_dir_is_refused(scratch):
    scratch.course("机器视觉")
    scratch.vault_course("别的课程")
    api = scratch.api()
    from wbl_core import paths as P
    with pytest.raises(P.PathNotAllowed):
        api.catalog.set_note_mapping("course:机器视觉", "不存在的笔记")
    with pytest.raises(api.HTTPException) as ei:
        api.set_notes(id="course:机器视觉", vault="不存在的笔记")
    assert ei.value.status_code == 403
    assert scratch.read_mapping() == {}, "被拒绝的请求不能写任何映射"


def test_selecting_vault_for_unknown_object_is_refused(scratch):
    scratch.course("机器视觉")
    scratch.vault_course("机器视觉")
    api = scratch.api()
    from wbl_core import paths as P
    with pytest.raises(P.PathNotAllowed):
        api.catalog.set_note_mapping("course:没有的课", "机器视觉")
    with pytest.raises(api.HTTPException) as ei:
        api.set_notes(id="course:没有的课", vault="机器视觉")
    assert ei.value.status_code == 403


def test_note_files_and_preview_are_scoped_to_object(scratch):
    scratch.course("课程A")
    scratch.course("课程B")
    scratch.vault_course("课程A", {"a.md": "# A 笔记", "课次/1.md": "# 课次一"})
    scratch.vault_course("课程B", {"b.md": "# B 笔记"})
    api = scratch.api()

    listing = api.note_files(id="course:课程A")
    assert listing["linked"] is True
    names = [f["rel_path"] for f in listing["files"]]
    assert any(n.endswith("课程A/a.md") for n in names)
    assert not any(n.endswith("课程B/b.md") for n in names), "不能列出别的课程的笔记"

    preview = api.note_preview(id="course:课程A", path="课程A/a.md")
    assert "A 笔记" in preview["text"]

    # 跨课程读：内核抛 PathNotAllowed，路由层翻译成 403
    with pytest.raises(api.HTTPException) as ei:
        api.note_preview(id="course:课程A", path="课程B/b.md")
    assert ei.value.status_code == 403
    assert "不属于当前对象" in str(ei.value.detail)


def test_unlinked_object_has_no_note_files(scratch):
    scratch.course("课程A")
    scratch.vault_course("课程B")           # 名称不同，不自动关联
    api = scratch.api()
    listing = api.note_files(id="course:课程A")
    assert listing["linked"] is False
    assert listing["files"] == []


def test_course_and_study_are_classified_separately(scratch):
    scratch.course("机器视觉")
    scratch.study_topic("RL教学", {"NOTES.md": "x"})
    scratch.study_topic("diffusion_policy", {".git/config": "[remote \"origin\"]\n\turl = https://github.com/real-stanford/diffusion_policy.git\n"})
    api = scratch.api()

    data = api.objects()
    courses = [o["name"] for o in data["courses"]]
    study = [o["name"] for o in data["study"]]
    assert "机器视觉" in courses and "RL教学" not in courses
    assert "RL教学" in study and "机器视觉" not in study
    assert all(o["kind"] == "course" for o in data["courses"])
    assert all(o["kind"] == "study" for o in data["study"])

    dp = next(o for o in data["study"] if o["name"] == "diffusion_policy")
    assert dp["git"]["remote_url"].endswith("diffusion_policy.git")


def test_stdin_git_config_without_origin_is_none(scratch):
    scratch.study_topic("无远端", {".git/config": "[core]\n\trepositoryformatversion = 0\n"})
    api = scratch.api()
    obj = next(o for o in api.objects()["objects"] if o["name"] == "无远端")
    assert obj["git"] is None
