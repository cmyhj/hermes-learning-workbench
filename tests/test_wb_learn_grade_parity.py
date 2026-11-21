"""前端 quiz.js 的判分与后端 quiz_schema.grade 必须**逐条一致**。

为什么值得单独一个文件：判分逻辑存在两份（前端离线判定、后端批处理/接口），
两份一旦漂移，用户在页面上看到「答对」而后端记录「答错」——这种 bug 在 UI 里
几乎看不出来，只能靠固定题表对比。这里用 node 真跑 dist/quiz.js（不是复制一份）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from wb_learn_support import scratch  # noqa: F401

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _quiz_js() -> Path:
    override = os.environ.get("WB_LEARN_DASHBOARD")
    if override:
        return Path(override) / "dist" / "quiz.js"
    for rel in (Path("plugins") / "wb-learn" / "dashboard", Path("wb-learn") / "dashboard"):
        candidate = REPO / rel / "dist" / "quiz.js"
        if candidate.is_file():
            return candidate
    raise RuntimeError("找不到 dist/quiz.js")


QUIZ_JS = _quiz_js()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 未安装")


def _js_grade(cases):
    """在 node 里加载 dist/quiz.js，对每个用例调 WBL_QUIZ.grade（cases 走 stdin）。"""
    script = """
const fs = require('fs');
global.window = { localStorage: { getItem(){return null;}, setItem(){} } };
const src = fs.readFileSync(process.argv[1], 'utf8');
const fn = new Function('window', 'globalThis', src + '\\n;return this.WBL_QUIZ || window.WBL_QUIZ;');
const Q = fn.call(globalThis, global.window, globalThis);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => Q.grade(c.question, c.chosen))));
"""
    payload = json.dumps(cases, ensure_ascii=False)
    proc = subprocess.run(
        ["node", "-e", script, "--", str(QUIZ_JS)],
        input=payload, capture_output=True, text=True, encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


CASES = [
    # single
    ({"type": "single", "answer": ["A"]}, "A"),
    ({"type": "single", "answer": ["A"]}, "a"),
    ({"type": "single", "answer": ["A"]}, "B"),
    ({"type": "single", "answer": ["A"]}, ""),
    # multiple：乱序 / 少选 / 多选
    ({"type": "multiple", "answer": ["A", "C"]}, ["C", "A"]),
    ({"type": "multiple", "answer": ["A", "C"]}, ["A"]),
    ({"type": "multiple", "answer": ["A", "C"]}, ["A", "C", "D"]),
    ({"type": "multiple", "answer": ["A", "B", "C"]}, ["a", "b", "c"]),
    # judge 归一
    ({"type": "judge", "answer": ["正确"]}, "对"),
    ({"type": "judge", "answer": ["正确"]}, "√"),
    ({"type": "judge", "answer": ["正确"]}, "T"),
    ({"type": "judge", "answer": ["正确"]}, "错误"),
    ({"type": "judge", "answer": ["错误"]}, "×"),
    ({"type": "judge", "answer": ["错误"]}, "F"),
    ({"type": "judge", "answer": ["错误"]}, ""),
    # blank：单空 / 多空 / 数量不符 / 等价写法
    ({"type": "blank", "blank_answers": [["测量"]]}, ["测量"]),
    ({"type": "blank", "blank_answers": [["测量"]]}, ["測量"]),
    ({"type": "blank", "blank_answers": [["测量"], ["判断"]]}, ["测量", "判断"]),
    ({"type": "blank", "blank_answers": [["测量"], ["判断"]]}, ["测量"]),
    ({"type": "blank", "blank_answers": [["测量", "measurement"]]}, ["measurement"]),
    # short：永远不判分
    ({"type": "short", "answer": ["参考答案"]}, ["参考答案"]),
    ({"type": "short", "answer": ["参考答案"]}, ["完全不相干"]),
]


@pytest.mark.parametrize("question,chosen", CASES)
def test_frontend_and_backend_grading_agree(scratch, question, chosen):
    api = scratch.api()
    py = api.schema.grade(question, chosen)

    js = _js_grade([{"question": question, "chosen": chosen}])[0]

    assert js["judged"] == py["judged"], f"judged 不一致：js={js} py={py}"
    assert js["correct"] == py["correct"], f"correct 不一致：js={js} py={py}"


def test_blank_per_blank_detail_matches(scratch):
    api = scratch.api()
    question = {"type": "blank", "blank_answers": [["测量"], ["判断"], ["控制"]]}
    chosen = ["测量", "错", "控制"]
    py = api.schema.grade(question, chosen)
    js = _js_grade([{"question": question, "chosen": chosen}])[0]

    assert [d["correct"] for d in py["detail"]] == [d["correct"] for d in js["detail"]]
    assert [d["index"] for d in py["detail"]] == [d["index"] for d in js["detail"]]


def test_extract_bank_pulls_json_from_agent_reply(scratch):
    """前端「从回复提取题库」必须能处理 ```json 代码块与裸 JSON，且抽不到就返回 null。"""
    script = """
const fs = require('fs');
global.window = { localStorage: { getItem(){return null;}, setItem(){} } };
const src = fs.readFileSync(process.argv[1], 'utf8');
const fn = new Function('window', 'globalThis', src + '\\n;return this.WBL_QUIZ || window.WBL_QUIZ;');
const Q = fn.call(globalThis, global.window, globalThis);
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(inputs.map(t => Q.extractBank(t))));
"""
    sample = {"name": "测试", "questions": [{"stem": "题干", "type": "judge", "answer": ["正确"]}]}
    inputs = [
        "好的，这是题库：\n```json\n" + json.dumps(sample, ensure_ascii=False) + "\n```\n希望有用。",
        json.dumps([{"stem": "数组题", "type": "judge", "answer": ["正确"]}], ensure_ascii=False),
        "这里没有 JSON。",
        "```json\n{ 坏掉的 json }\n```",
    ]
    proc = subprocess.run(
        ["node", "-e", script, "--", str(QUIZ_JS)],
        input=json.dumps(inputs, ensure_ascii=False), capture_output=True,
        text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out[0]["questions"][0]["stem"] == "题干"
    assert out[1]["questions"][0]["stem"] == "数组题"
    assert out[2] is None
    assert out[3] is None
