"""题库标准格式（schema v1）+ 校验 + 既有 txt 题库解析。

## 为什么自己定 schema
上游 ``reiqr/shiroha-quiz`` 是 **GPL-3.0**，本仓库不分发它的任何代码；这里只做
**格式兼容**（能读它的 JSON 题面形状），不复制实现。字段命名按本仓库需要重新设计，
理由写在 ``docs/QUIZ_SCHEMA.md``。

## 标准 JSON（``quiz-library/<name>.json``）
```
{
  "schema_version": 1,
  "name": "机器视觉 · 第3章",          // 可选，缺省用文件名
  "object_id": "course:机器视觉",       // 可选，生成时写入
  "generated": {"by": "hermes-agent", "at": "2026-11-21T00:00:00", "model": null},
  "questions": [
    {
      "id": "q-0001",
      "type": "single",                // single|multiple|judge|blank|short
      "stem": "题干",
      "options": [{"key": "A", "text": "选项"}],
      "answer": ["A"],                  // 见下「答案形状」
      "blank_answers": [["主答案","等价写法"]],   // 仅 blank，多空时每空一组
      "explanation": "解析（只允许来自材料原文）",
      "source": {"path": "2026-10/实验1/相机标定.pdf", "locator": "p.12"},
      "points": 1,
      "tags": []
    }
  ]
}
```

### 答案形状（判分实现严格按这张表，不猜）
| type | answer | 判分 |
|---|---|---|
| single | ``["A"]`` | 选项字母集合完全相等 |
| multiple | ``["A","C"]`` | 选项字母**集合**相等（无序） |
| judge | ``["正确"]`` / ``["错误"]`` | 归一后完全相等（对/√/T → 正确） |
| blank | ``["答案"]`` 或配 ``blank_answers`` | 每个空与「该空任一可接受写法」归一后相等 |
| short | ``["参考答案"]`` | **不自动判分**；只展示参考答案，由用户自评 |

### 兼容
* 读入时接受 ``question``/``answerKeys``/``analysis``/``topic`` 等上游命名；
* ``options`` 也接受 ``{"A": "文本"}`` 的映射写法；
* 缺 ``id`` 时按顺序补 ``q-0001``；``type`` 缺失时按「有选项+单字母答案→single」等推断，
  推断不出来的题**直接报错**，不塞进去当猜的。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
TYPES = ("single", "multiple", "judge", "blank", "short")

_OPT_KEY_RE = re.compile(r"^[A-H]$")
_TRUE_WORDS = {"正确", "对", "是", "true", "t", "√", "a", "1"}
_FALSE_WORDS = {"错误", "错", "否", "false", "f", "×", "b", "0"}
_WS_RE = re.compile(r"\s+")
_PUNCT = "，,。.；;：:、（）()【】[]“”\"'‘’!！?？"


class SchemaError(Exception):
    """题库结构非法。带 ``issues`` 供前端逐条展示。"""

    def __init__(self, issues: List[str]):
        self.issues = issues
        super().__init__("；".join(issues[:5]) or "题库结构非法")


# --------------------------------------------------------------------------
# 归一化小工具
# --------------------------------------------------------------------------
def norm_text(value: Any) -> str:
    """文本比较归一：去空白、去中文/英文标点、转小写。只用于**答案比对**。"""
    text = str(value if value is not None else "")
    text = _WS_RE.sub("", text)
    text = "".join(ch for ch in text if ch not in _PUNCT)
    return text.lower()


def norm_judge(value: Any) -> Optional[str]:
    text = norm_text(value)
    if text in _TRUE_WORDS:
        return "正确"
    if text in _FALSE_WORDS:
        return "错误"
    return None


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                out.extend(str(x) for x in item if str(x).strip())
            elif item is not None and str(item).strip():
                out.append(str(item))
        return out
    return [str(value)]


def _options(value: Any) -> List[Dict[str, str]]:
    """选项归一成 ``[{"key","text"}]``，保持 A..H 顺序、去重。"""
    items: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        for key, text in value.items():
            items.append((str(key).strip().upper(), str(text).strip()))
    elif isinstance(value, (list, tuple)):
        for idx, opt in enumerate(value):
            if isinstance(opt, dict):
                key = str(opt.get("key") or opt.get("label") or "").strip().upper()
                text = opt.get("text", opt.get("content", opt.get("value", "")))
                if not key:
                    key = chr(ord("A") + idx)
                items.append((key, str(text).strip()))
            else:
                items.append((chr(ord("A") + idx), str(opt).strip()))
    seen = {}
    out: List[Dict[str, str]] = []
    for key, text in items:
        if not _OPT_KEY_RE.match(key) or key in seen or not text:
            continue
        seen[key] = True
        out.append({"key": key, "text": text})
    out.sort(key=lambda o: o["key"])
    return out


def _pick(raw: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in raw and raw[n] not in (None, ""):
            return raw[n]
    return None


# --------------------------------------------------------------------------
# 单题归一 + 校验
# --------------------------------------------------------------------------
def normalize_question(raw: Any, index: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise SchemaError([f"第 {index} 题不是对象"])
    label = f"第 {index} 题"

    stem = str(_pick(raw, "stem", "question", "title") or "").strip()
    if not stem:
        raise SchemaError([f"{label} 缺少题干"])

    options = _options(_pick(raw, "options", "choices") or [])
    answer_raw = _pick(raw, "answer", "answers", "answerKeys", "correct", "correctAnswer",
                       "rightAnswer", "参考答案", "正确答案", "答案")
    answer = _as_list(answer_raw)
    blank_groups_raw = _pick(raw, "blank_answers", "blankAnswers", "答案分组")

    qtype = str(_pick(raw, "type", "qtype") or "").strip().lower()
    qtype = _TYPE_ALIAS.get(qtype, qtype)
    if qtype not in TYPES:
        # 只在证据明确时推断，不硬猜
        letters = [a.strip().upper() for a in answer if _OPT_KEY_RE.match(a.strip().upper())]
        if options and len(letters) == 1:
            qtype = "single"
        elif options and len(letters) > 1:
            qtype = "multiple"
        elif len(answer) == 1 and norm_judge(answer[0]) and not options:
            qtype = "judge"
        elif blank_groups_raw:
            qtype = "blank"
        else:
            raise SchemaError([f"{label} 题型缺失且无法从结构推断"])

    issues: List[str] = []
    out: Dict[str, Any] = {
        "id": str(_pick(raw, "id") or f"q-{index:04d}"),
        "type": qtype,
        "stem": stem,
        "options": options,
        "answer": answer,
        "points": raw.get("points", raw.get("score", 1)),
        "tags": _as_list(raw.get("tags") or raw.get("knowledgePoints") or []),
    }

    if qtype in ("single", "multiple"):
        if len(options) < 2:
            issues.append(f"{label} 选择题选项少于 2 个")
        letters = [a.strip().upper() for a in answer]
        if not letters:
            issues.append(f"{label} 选择题没有答案")
        bad = [x for x in letters if not _OPT_KEY_RE.match(x)]
        if bad:
            issues.append(f"{label} 答案不是选项字母：{','.join(bad)}")
        unknown = [x for x in letters if x not in {o['key'] for o in options}]
        if unknown:
            issues.append(f"{label} 答案超出已有选项：{','.join(unknown)}")
        if qtype == "single" and len(set(letters)) not in (1,):
            issues.append(f"{label} 单选却有 {len(set(letters))} 个答案（应为 multiple）")
        out["answer"] = sorted(set(letters))
    elif qtype == "judge":
        norm = [norm_judge(a) for a in answer]
        norm = [x for x in norm if x]
        if len(norm) != 1:
            issues.append(f"{label} 判断题答案必须是 正确/错误")
        else:
            out["answer"] = norm
    elif qtype == "blank":
        groups = _blank_groups(blank_groups_raw, answer)
        if not groups:
            issues.append(f"{label} 填空题没有答案")
        out["blank_answers"] = groups
        out["answer"] = [g[0] for g in groups if g]
    elif qtype == "short":
        if not answer:
            issues.append(f"{label} 简答题没有参考答案（无答案不得作为简答题入库）")

    explanation = _pick(raw, "explanation", "analysis", "parse", "解析")
    if explanation is not None:
        text = str(explanation).strip()
        if text:
            out["explanation"] = text

    source = raw.get("source")
    out["source"] = None
    if isinstance(source, dict):
        path = str(source.get("path") or "").strip()
        if path:
            out["source"] = {"path": path, "locator": str(source.get("locator") or "").strip() or None}
    elif isinstance(source, str) and source.strip():
        out["source"] = {"path": source.strip(), "locator": None}

    out["warnings"] = issues
    # 每题的结构问题（答案越界、判断答案非法、缺参考答案…）都算**致命**：
    # 这样的题进库只会让人做不出正确答案，所以题库整体 ok=False，不允许保存。
    out["issues"] = list(issues)
    return out


_TYPE_ALIAS = {
    "单选": "single", "单选题": "single", "choice": "single", "single_choice": "single",
    "多选": "multiple", "多选题": "multiple", "multi": "multiple", "multiple_choice": "multiple",
    "判断": "judge", "判断题": "judge", "true_false": "judge", "tf": "judge",
    "填空": "blank", "填空题": "blank", "fill": "blank", "fill_blank": "blank", "cloze": "blank",
    "简答": "short", "简答题": "short", "short_answer": "short", "essay": "short",
}


def _blank_groups(raw: Any, answer: List[str]) -> List[List[str]]:
    """多空填空题的每空可接受写法。没有分组时按分号/顿号切 answer。"""
    groups: List[List[str]] = []
    if isinstance(raw, (list, tuple)) and raw:
        for item in raw:
            variants = [str(x).strip() for x in (item if isinstance(item, (list, tuple)) else [item])
                        if str(x).strip()]
            if variants:
                groups.append(variants)
    if groups:
        return groups
    if len(answer) > 1:
        return [[a] for a in answer]
    if len(answer) == 1:
        parts = [p.strip() for p in re.split(r"[;；]", answer[0]) if p.strip()]
        return [[p] for p in parts] if len(parts) > 1 else [[answer[0].strip()]]
    return []


# --------------------------------------------------------------------------
# 整库校验
# --------------------------------------------------------------------------
def validate_bank(data: Any) -> Dict[str, Any]:
    """校验整个题库；返回 ``{ok, questions, issues, warnings, counts}``。

    ``ok=False`` 时 ``questions`` 仍带着能通过校验的题 —— 草稿预览要能看到「哪几道有问题」。
    """
    issues: List[str] = []
    questions: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if isinstance(data, list):
        raw_questions, meta = data, {}
    elif isinstance(data, dict):
        raw_questions = data.get("questions")
        meta = data
    else:
        raise SchemaError(["题库必须是对象或数组"])

    if not isinstance(raw_questions, list) or not raw_questions:
        # 空题库不当异常抛：草稿预览要能显示「为什么不能保存」，而不是一个 422 了事
        return {
            "ok": False, "schema_version": SCHEMA_VERSION,
            "name": str(meta.get("name") or "") or None,
            "object_id": str(meta.get("object_id") or "") or None,
            "generated": meta.get("generated") if isinstance(meta.get("generated"), dict) else None,
            "questions": [], "counts": {t: 0 for t in TYPES}, "total": 0,
            "issues": ["题库缺少 questions 数组，或数组为空"], "warnings": [],
        }

    if meta.get("schema_version") not in (None, SCHEMA_VERSION):
        issues.append(f"schema_version 不是 {SCHEMA_VERSION}（收到 {meta.get('schema_version')}）")

    seen_ids = set()
    for idx, raw in enumerate(raw_questions, start=1):
        try:
            q = normalize_question(raw, idx)
        except SchemaError as exc:
            issues.extend(exc.issues)
            continue
        warnings.extend(q.pop("warnings", []))
        issues.extend(q.pop("issues", []))
        qid = q["id"]
        if qid in seen_ids:
            qid = f"{qid}-{idx}"
            q["id"] = qid
        seen_ids.add(qid)
        questions.append(q)

    counts = {t: sum(1 for q in questions if q["type"] == t) for t in TYPES}
    return {
        "ok": not issues,
        "schema_version": SCHEMA_VERSION,
        "name": str(meta.get("name") or "") or None,
        "object_id": str(meta.get("object_id") or "") or None,
        "generated": meta.get("generated") if isinstance(meta.get("generated"), dict) else None,
        "questions": questions,
        "counts": counts,
        "issues": issues,
        "warnings": warnings,
        "total": len(questions),
    }


# --------------------------------------------------------------------------
# 判分（唯一实现，前端/后端共用同一套规则）
# --------------------------------------------------------------------------
def grade(question: Dict[str, Any], chosen: Any) -> Dict[str, Any]:
    """返回 ``{judged, correct, detail}``；``judged=False`` 表示该题型不自动判分。"""
    qtype = question.get("type")
    if qtype == "short":
        return {"judged": False, "correct": None,
                "detail": "简答题不自动判分：请对照参考答案自评（不做关键词假判）。"}

    if qtype in ("single", "multiple"):
        picked = {str(x).strip().upper() for x in _as_list(chosen)}
        expected = {str(x).strip().upper() for x in question.get("answer") or []}
        return {"judged": True, "correct": picked == expected,
                "detail": f"你的答案：{''.join(sorted(picked)) or '未作答'}｜"
                          f"参考答案：{''.join(sorted(expected))}"}

    if qtype == "judge":
        picked = [norm_judge(x) for x in _as_list(chosen)]
        picked = [x for x in picked if x]
        expected = [norm_judge(x) for x in question.get("answer") or []]
        expected = [x for x in expected if x]
        ok = len(picked) == 1 and picked == expected
        return {"judged": True, "correct": ok,
                "detail": f"你的答案：{picked[0] if picked else '未作答'}｜"
                          f"参考答案：{expected[0] if expected else '未提供'}"}

    groups = question.get("blank_answers") or []
    values = _as_list(chosen)
    if len(values) != len(groups):
        return {"judged": True, "correct": False,
                "detail": f"填空数量不符：需要填 {len(groups)} 个空，收到 {len(values)} 个"}
    per_blank = []
    for idx, accepted in enumerate(groups):
        actual = norm_text(values[idx])
        ok = bool(actual) and any(norm_text(a) == actual for a in accepted)
        per_blank.append({"index": idx + 1, "user": values[idx], "accepted": accepted, "correct": ok})
    return {"judged": True, "correct": all(b["correct"] for b in per_blank),
            "detail": per_blank}


# --------------------------------------------------------------------------
# 既有纯文本题库（机器视觉题库格式）解析 —— 不改写用户题库，只读
# --------------------------------------------------------------------------
_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、\s*(\S+?题)\s*$", re.M)
_NUM_RE = re.compile(r"^(\d+)[.．]\s*(.*)$")
_OPT_RE = re.compile(r"^([A-H])[.．]\s*(.*)$")
_ANS_PREFIX = ("答案：", "答案:", "正确答案：", "正确答案:", "参考答案：", "参考答案:")


def parse_text_bank(text: str, source_path: str = "") -> Dict[str, Any]:
    """解析「一、单选题 / 1. 题干 / A. 选项 / 答案：B / 解析：…」这类既有题库。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    marks = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(text)]
    sections: List[Tuple[str, str]] = []
    if not marks:
        sections.append(("单选题", text))
    else:
        for i, (pos, name) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            sections.append((name, text[pos:end]))

    questions: List[Dict[str, Any]] = []
    issues: List[str] = []
    for section, block in sections:
        qtype = _section_type(section)
        parsed = _parse_block(block)
        if not parsed:
            continue
        for item in parsed:
            raw = {
                "type": qtype,
                "stem": item["stem"],
                "options": item["options"],
                "answer": item["answer"],
                "explanation": item.get("explain"),
                "source": {"path": source_path, "locator": f"{section} 第{item['n']}题"} if source_path else None,
            }
            try:
                q = normalize_question(raw, len(questions) + 1)
            except SchemaError as exc:
                issues.extend(exc.issues)
                continue
            q.pop("warnings", None)
            q["section"] = section
            q["n"] = item["n"]
            questions.append(q)
    return {"questions": questions, "issues": issues,
            "sections": [s for s, _ in sections], "source": source_path}


def _section_type(name: str) -> str:
    if "多选" in name:
        return "multiple"
    if "单选" in name or "选择" in name:
        return "single"
    if "判断" in name:
        return "judge"
    if "填空" in name:
        return "blank"
    return "short"


def _parse_block(block: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    mode = "stem"

    def flush() -> None:
        if not current:
            return
        stem = " ".join(x for x in current["stem"] if x).strip()
        if not stem:
            return
        answer = current.get("answer")
        out.append({
            "n": current["n"],
            "stem": stem,
            "options": current["options"],
            "answer": [answer] if answer else [],
            "explain": " ".join(current.get("explain") or []).strip() or None,
        })

    for raw in block.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _NUM_RE.match(line)
        if m:
            flush()
            current = {"n": int(m.group(1)), "stem": [m.group(2)], "options": {}}
            mode = "stem"
            continue
        if current is None:
            continue
        hit = next((p for p in _ANS_PREFIX if line.startswith(p)), None)
        if hit:
            current["answer"] = line[len(hit):].strip()
            mode = "answer"
            continue
        if line.startswith(("解析：", "解析:")):
            current["explain"] = [line.split("：", 1)[-1].split(":", 1)[-1].strip()]
            mode = "explain"
            continue
        if mode == "explain":
            current["explain"].append(line)
            continue
        om = _OPT_RE.match(line)
        if om:
            current["options"][om.group(1)] = om.group(2).strip()
            mode = "options"
            continue
        if mode == "stem":
            current["stem"].append(line)
        else:
            current.setdefault("explain", []).append(line)
            mode = "explain"
    flush()
    return out
