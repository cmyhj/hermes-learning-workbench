"""wb-learn 题库：schema 校验、五种题型判分、生成草稿 → 确认保存。

判分要求（需求原文）：判断与多选必须**真实支持**，主观题不许关键词假判。
这里逐条钉：多选乱序算对、少选算错、判断题归一、填空多空逐空、简答不判分。
"""

from __future__ import annotations

import json

import pytest

from wb_learn_support import SAMPLE_BANK, TEXT_BANK, scratch  # noqa: F401


def _bank(**overrides):
    data = json.loads(json.dumps(SAMPLE_BANK))
    data.update(overrides)
    return data


# ---------------------------------------------------------------- schema 校验
def test_valid_sample_bank_passes(scratch):
    api = scratch.api()
    result = api.schema.validate_bank(_bank())
    assert result["ok"] is True
    assert result["total"] == 5
    assert result["counts"] == {"single": 1, "multiple": 1, "judge": 1, "blank": 1, "short": 1}
    assert result["issues"] == []


@pytest.mark.parametrize("bad,fragment", [
    ({"questions": []}, "数组为空"),
    ({}, "缺少 questions"),
    ({"questions": [{"stem": ""}]}, "缺少题干"),
    ({"questions": [{"stem": "x", "type": "single",
                     "options": {"A": "a", "B": "b"}, "answer": ["Z"]}]}, "超出已有选项"),
    ({"questions": [{"stem": "x", "type": "single", "options": {"A": "a"}, "answer": ["A"]}]}, "选项少于 2"),
    ({"questions": [{"stem": "x", "type": "judge", "answer": ["可能吧"]}]}, "必须是 正确/错误"),
    ({"questions": [{"stem": "x", "type": "short"}]}, "没有参考答案"),
    ({"questions": [{"stem": "x", "type": "mystery"}]}, "无法从结构推断"),
    ("不是对象", "必须是对象或数组"),
])
def test_invalid_banks_report_issues(scratch, bad, fragment):
    api = scratch.api()
    if isinstance(bad, str):
        with pytest.raises(api.schema.SchemaError) as ei:
            api.schema.validate_bank(bad)
        assert fragment in "；".join(ei.value.issues)
        return
    result = api.schema.validate_bank(bad)
    assert result["ok"] is False
    joined = "；".join(result["issues"])
    assert fragment in joined, joined


def test_single_with_two_answers_is_flagged(scratch):
    api = scratch.api()
    result = api.schema.validate_bank({"questions": [{
        "stem": "x", "type": "single",
        "options": {"A": "a", "B": "b", "C": "c"}, "answer": ["A", "B"],
    }]})
    assert result["ok"] is False
    assert any("multiple" in i for i in result["issues"])


def test_upstream_field_names_are_accepted(scratch):
    """兼容 Shiroha 风格字段名（question / answerKeys / analysis / topic），但不复制其代码。"""
    api = scratch.api()
    result = api.schema.validate_bank({"questions": [{
        "question": "题干", "type": "multiple", "topic": "章节",
        "options": {"A": "甲", "B": "乙", "C": "丙"},
        "answerKeys": ["A", "C"], "analysis": "解析文本",
    }]})
    assert result["ok"] is True
    q = result["questions"][0]
    assert q["stem"] == "题干"
    assert q["answer"] == ["A", "C"]
    assert q["explanation"] == "解析文本"
    assert q["source"] is None, "没给 source 就不编造来源"


def test_schema_version_mismatch_is_reported(scratch):
    api = scratch.api()
    result = api.schema.validate_bank({"schema_version": 99, "questions": SAMPLE_BANK["questions"]})
    assert result["ok"] is False
    assert any("schema_version" in i for i in result["issues"])


# ---------------------------------------------------------------- 判分
def test_multiple_choice_order_does_not_matter(scratch):
    api = scratch.api()
    q = api.schema.validate_bank(_bank())["questions"][1]     # multiple, answer A B C
    assert api.schema.grade(q, ["C", "A", "B"])["correct"] is True
    assert api.schema.grade(q, ["A", "B"])["correct"] is False, "少选必须判错"
    assert api.schema.grade(q, ["A", "B", "C", "D"])["correct"] is False, "多选必须判错"
    assert api.schema.grade(q, [])["correct"] is False


def test_single_choice_needs_exact_letter(scratch):
    api = scratch.api()
    q = api.schema.validate_bank(_bank())["questions"][0]
    assert api.schema.grade(q, "A")["correct"] is True
    assert api.schema.grade(q, ["a"])["correct"] is True, "大小写归一"
    assert api.schema.grade(q, "B")["correct"] is False


@pytest.mark.parametrize("chosen,expected", [
    ("错误", True), (["错误"], True), ("错", True), ("×", True), ("F", True),
    ("正确", False), ("对", False), ("√", False), ("", False),
])
def test_judge_normalisation(scratch, chosen, expected):
    api = scratch.api()
    q = api.schema.validate_bank(_bank())["questions"][2]     # judge, answer 错误
    assert api.schema.grade(q, chosen)["correct"] is expected


def test_blank_requires_every_blank_and_accepts_variants(scratch):
    api = scratch.api()
    bank = api.schema.validate_bank({"questions": [{
        "stem": "三个动作____、____、____", "type": "blank",
        "blank_answers": [["测量", "measurement"], ["判断"], ["控制"]],
    }]})
    q = bank["questions"][0]
    assert api.schema.grade(q, ["测量", "判断", "控制"])["correct"] is True
    assert api.schema.grade(q, ["measurement", "判断", "控制"])["correct"] is True
    assert api.schema.grade(q, ["测量", "判断"])["correct"] is False, "空数不符要判错"
    assert api.schema.grade(q, ["测量", "判断", ""])["correct"] is False
    detail = api.schema.grade(q, ["测量", "错的", "控制"])["detail"]
    assert [d["correct"] for d in detail] == [True, False, True]


def test_short_answer_is_never_auto_judged(scratch):
    """主观题不许关键词假判：judged=False，答案只作参考展示。"""
    api = scratch.api()
    q = api.schema.validate_bank(_bank())["questions"][4]
    for chosen in (["张正友"], ["用平面标定板"], ["完全无关的话"], []):
        out = api.schema.grade(q, chosen)
        assert out["judged"] is False
        assert out["correct"] is None
        assert "自评" in out["detail"]


def test_grade_endpoint_single_and_batch(scratch):
    api = scratch.api()
    one = api.quiz_grade({"question": SAMPLE_BANK["questions"][0], "chosen": "A"})
    assert one["results"][0]["correct"] is True

    batch = api.quiz_grade({
        "questions": SAMPLE_BANK["questions"],
        "answers": {"q-1": "A", "q-2": ["A", "B", "C"], "q-3": ["错误"],
                    "q-4": ["测量", "判断", "控制"], "q-5": ["随便写写"]},
    })
    assert batch["judged"] == 4
    assert batch["correct"] == 4
    assert batch["manual"] == ["q-5"], "简答题必须归入 manual 而不是判对/判错"


# ---------------------------------------------------------------- 既有 txt 格式
def test_existing_text_bank_parses(scratch):
    api = scratch.api()
    parsed = api.schema.parse_text_bank(TEXT_BANK, "复习题库.txt")
    assert parsed["issues"] == []
    types = [q["type"] for q in parsed["questions"]]
    assert types == ["single", "judge", "blank", "short"]
    assert parsed["questions"][0]["answer"] == ["B"]
    assert parsed["questions"][0]["source"]["path"] == "复习题库.txt"
    assert "单选题" in parsed["questions"][0]["source"]["locator"]


def test_text_bank_grade_uses_same_rules(scratch):
    api = scratch.api()
    parsed = api.schema.parse_text_bank(TEXT_BANK)
    single = parsed["questions"][0]
    assert api.schema.grade(single, "B")["correct"] is True
    blank = parsed["questions"][2]
    assert api.schema.grade(blank, ["测量", "判断", "控制"])["correct"] is True
    short = parsed["questions"][3]
    assert api.schema.grade(short, ["求内外参"])["judged"] is False


# ---------------------------------------------------------------- 题库目录读取
def test_no_quiz_library_is_empty_not_fabricated(scratch):
    scratch.course("课程A", {"第1章.md": "x"})
    api = scratch.api()
    listed = api.quiz_banks(id="course:课程A")
    assert listed["count"] == 0
    assert listed["banks"] == []
    assert listed["quiz_library"]["exists"] is False


def test_json_and_text_banks_are_both_listed(scratch):
    d = scratch.course("课程A")
    scratch.write(d / "quiz-library" / "标准.json", _bank())
    scratch.write(d / "quiz-library" / "复习.txt", TEXT_BANK)
    api = scratch.api()

    listed = api.quiz_banks(id="course:课程A")
    assert listed["count"] == 2
    by_id = {b["id"]: b for b in listed["banks"]}
    assert by_id["标准"]["format"] == "json" and by_id["标准"]["ok"] is True
    assert by_id["标准"]["total"] == 5
    assert by_id["复习"]["format"] == "text" and by_id["复习"]["read_only"] is True
    assert by_id["复习"]["total"] == 4


def test_broken_bank_is_visible_with_error(scratch):
    d = scratch.course("课程A")
    scratch.write(d / "quiz-library" / "坏库.json", {"questions": [{"stem": ""}]})
    api = scratch.api()
    listed = api.quiz_banks(id="course:课程A")
    bank = listed["banks"][0]
    assert bank["ok"] is False
    assert bank["issues"] or bank.get("parse_error")


def test_bank_id_must_come_from_listing(scratch):
    d = scratch.course("课程A")
    scratch.write(d / "quiz-library" / "标准.json", _bank())
    api = scratch.api()
    with pytest.raises(api.HTTPException) as ei:
        api.quiz_bank(id="course:课程A", bank="../标准")
    assert ei.value.status_code == 404
    with pytest.raises(api.HTTPException) as ei2:
        api.quiz_bank(id="course:课程A", bank="C:/Windows/win.ini")
    assert ei2.value.status_code == 404


def test_bank_type_filter_and_limit(scratch):
    d = scratch.course("课程A")
    scratch.write(d / "quiz-library" / "标准.json", _bank())
    api = scratch.api()
    single = api.quiz_bank(id="course:课程A", bank="标准", type="single")
    assert single["returned"] == 1
    assert single["questions"][0]["type"] == "single"
    limited = api.quiz_bank(id="course:课程A", bank="标准", limit=2)
    assert limited["returned"] == 2
    assert api.quiz_bank(id="course:课程A", bank="标准", limit=-5)["returned"] == 1


def test_bank_grading_contract_is_declared(scratch):
    d = scratch.course("课程A")
    scratch.write(d / "quiz-library" / "标准.json", _bank())
    api = scratch.api()
    payload = api.quiz_bank(id="course:课程A", bank="标准")
    assert payload["grading"]["manual"] == ["short"]
    assert set(payload["grading"]["auto"]) == {"single", "multiple", "judge", "blank"}
    assert "简答" in payload["grading"]["note"]
