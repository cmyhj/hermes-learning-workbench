/*
 * wb-learn 前端 · 题库做题
 *
 * 判分规则与后端 wbl_core/quiz_schema.py 的 grade() **逐条对齐**（tests/test_wb_learn_grade_parity.py
 * 用同一组固定题比对两边结果）：
 *   single   选项字母集合完全相等
 *   multiple 选项字母集合相等（无序，多选少选都算错）
 *   judge    归一后相等（正确/对/√/T/true → 正确；错误/错/×/F/false → 错误）
 *   blank    每个空与「该空任一可接受写法」归一后相等；空数量不符直接判错
 *   short    **不判分**，只展示参考答案让用户自评 —— 不做关键词假判
 *
 * 进度（已答/正确/错题 id）存 localStorage，作用域 = 对象 + 题库文件，切题库不串。
 */
(function (root) {
  "use strict";

  var PROGRESS_KEY = "wbl.progress.v1";
  var PUNCT = "，,。.；;：:、（）()【】[]“”\"'‘’!！?？";
  var TRUE_WORDS = ["正确", "对", "是", "true", "t", "√", "a", "1"];
  var FALSE_WORDS = ["错误", "错", "否", "false", "f", "×", "b", "0"];

  function normText(value) {
    var text = String(value == null ? "" : value).replace(/\s+/g, "");
    var out = "";
    for (var i = 0; i < text.length; i++) {
      if (PUNCT.indexOf(text[i]) < 0) out += text[i];
    }
    return out.toLowerCase();
  }

  function normJudge(value) {
    var t = normText(value);
    if (TRUE_WORDS.indexOf(t) >= 0) return "正确";
    if (FALSE_WORDS.indexOf(t) >= 0) return "错误";
    return null;
  }

  function asList(value) {
    if (value == null) return [];
    if (Array.isArray(value)) {
      var out = [];
      value.forEach(function (v) {
        if (Array.isArray(v)) v.forEach(function (x) { if (String(x).trim()) out.push(String(x)); });
        else if (String(v).trim()) out.push(String(v));
      });
      return out;
    }
    return [String(value)];
  }

  /** 与后端 grade() 等价。返回 {judged, correct, detail}。 */
  function grade(q, chosen) {
    var type = q && q.type;
    if (type === "short") {
      return { judged: false, correct: null,
               detail: "简答题不自动判分：请对照参考答案自评（不做关键词假判）。" };
    }
    if (type === "single" || type === "multiple") {
      var picked = {};
      asList(chosen).forEach(function (x) { picked[String(x).trim().toUpperCase()] = 1; });
      var expected = {};
      asList(q.answer).forEach(function (x) { expected[String(x).trim().toUpperCase()] = 1; });
      var a = Object.keys(picked).sort(), b = Object.keys(expected).sort();
      return { judged: true, correct: a.join("") === b.join(""),
               detail: "你的答案：" + (a.join("") || "未作答") + "｜参考答案：" + b.join("") };
    }
    if (type === "judge") {
      var p = asList(chosen).map(normJudge).filter(Boolean);
      var e = asList(q.answer).map(normJudge).filter(Boolean);
      return { judged: true, correct: p.length === 1 && p[0] === e[0],
               detail: "你的答案：" + (p[0] || "未作答") + "｜参考答案：" + (e[0] || "未提供") };
    }
    var groups = q.blank_answers || [];
    var values = asList(chosen);
    if (values.length !== groups.length) {
      return { judged: true, correct: false,
               detail: "填空数量不符：需要填 " + groups.length + " 个空，收到 " + values.length + " 个" };
    }
    var per = groups.map(function (accepted, index) {
      var actual = normText(values[index]);
      var ok = !!actual && accepted.some(function (x) { return normText(x) === actual; });
      return { index: index + 1, user: values[index], accepted: accepted, correct: ok };
    });
    return { judged: true, correct: per.every(function (x) { return x.correct; }), detail: per };
  }

  // ---------------------------------------------------------------- 进度
  function readProgress() {
    try {
      var raw = root.localStorage.getItem(PROGRESS_KEY);
      var data = raw ? JSON.parse(raw) : {};
      return data && typeof data === "object" ? data : {};
    } catch (e) { return {}; }
  }

  function writeProgress(map) {
    try { root.localStorage.setItem(PROGRESS_KEY, JSON.stringify(map || {})); } catch (e) { /* 隐私模式 */ }
  }

  function scopeKey(objectId, bankId) { return String(objectId) + "::" + String(bankId); }

  function getScope(objectId, bankId) {
    var all = readProgress();
    var entry = all[scopeKey(objectId, bankId)];
    if (!entry || typeof entry !== "object") entry = {};
    return { answered: entry.answered || {}, cursor: entry.cursor || 0 };
  }

  function putProgress(objectId, bankId, patch) {
    var all = readProgress();
    var key = scopeKey(objectId, bankId);
    var entry = all[key] || { answered: {}, cursor: 0 };
    all[key] = {
      answered: patch.answered || entry.answered || {},
      cursor: typeof patch.cursor === "number" ? patch.cursor : (entry.cursor || 0),
      updated_at: new Date().toISOString(),
    };
    writeProgress(all);
    return all[key];
  }

  function stats(answered) {
    var ids = Object.keys(answered || {});
    var correct = ids.filter(function (id) { return answered[id] && answered[id].correct === true; }).length;
    return { answered: ids.length, correct: correct, wrong: ids.length - correct };
  }

  /** 从 agent 回复文本里抽出题库 JSON（```json 代码块或裸 JSON）。抽不到返回 null。 */
  function extractBank(text) {
    if (!text) return null;
    var blocks = [];
    var re = /```(?:json)?\s*([\s\S]*?)```/gi, m;
    while ((m = re.exec(text)) !== null) blocks.push(m[1]);
    blocks.push(text);
    for (var i = 0; i < blocks.length; i++) {
      var body = blocks[i].trim();
      // 取**最靠前**的括号作为起点：裸数组里第一个 "{" 属于数组元素，不能当成对象起点
      var objStart = body.indexOf("{"), arrStart = body.indexOf("[");
      var start, end;
      if (arrStart >= 0 && (objStart < 0 || arrStart < objStart)) {
        start = arrStart;
        end = body.lastIndexOf("]");
      } else if (objStart >= 0) {
        start = objStart;
        end = body.lastIndexOf("}");
      } else {
        continue;
      }
      if (start < 0 || end <= start) continue;
      try {
        var data = JSON.parse(body.slice(start, end + 1));
        var qs = Array.isArray(data) ? data : data.questions;
        if (Array.isArray(qs) && qs.length) return Array.isArray(data) ? { questions: data } : data;
      } catch (e) { /* 试下一块 */ }
    }
    return null;
  }

  root.WBL_QUIZ = {
    grade: grade,
    normText: normText,
    normJudge: normJudge,
    getScope: getScope,
    putProgress: putProgress,
    stats: stats,
    extractBank: extractBank,
  };
})(typeof window !== "undefined" ? window : globalThis);
