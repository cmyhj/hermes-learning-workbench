/*
 * wb-learn 前端 · 展示组件（无请求、无副作用）
 *
 * 约定：每个组件只接收 props，不自己 fetch、不自己写 localStorage。
 * 数据加载与写入留在 index.js，方便一眼看清「哪次点击会写盘」。
 * 纯 React.createElement，无 JSX / 无 import（desktop 与 dashboard 插件都不支持）。
 */
(function (root) {
  "use strict";

  var TYPE_LABEL = { single: "单选", multiple: "多选", judge: "判断", blank: "填空", short: "简答" };
  var NS = "wbl";

  function make(SDK) {
    var React = SDK.React;
    var h = React.createElement;
    var useState = SDK.hooks.useState;
    var useEffect = SDK.hooks.useEffect;

    // ---------------------------------------------------------------- 基础件
    function Empty(props) {
      return h("div", { className: NS + "-empty" },
        h("p", { className: NS + "-empty-title" }, props.title),
        props.hint ? h("p", { className: NS + "-empty-hint" }, props.hint) : null,
        props.children || null);
    }

    function Tag(props) {
      return h("span", { className: NS + "-tag" + (props.tone ? " " + NS + "-tag-" + props.tone : ""),
                         title: props.title || undefined }, props.children);
    }

    function NoteBadge(props) {
      var notes = props.notes || {};
      if (notes.linked) {
        return h(Tag, { tone: notes.kind === "exact" ? "ok" : "info",
                        title: "笔记目录：" + (notes.vault_path || notes.vault) },
          notes.kind === "exact" ? "笔记·同名" : "笔记·已指");
      }
      if (notes.stale) return h(Tag, { tone: "warn", title: notes.reason }, "笔记·失效");
      return h(Tag, { tone: "muted", title: notes.reason || "未关联笔记" }, "无笔记");
    }

    function Spinner(props) { return h("p", { className: NS + "-hint" }, props.label || "加载…"); }

    function Err(props) {
      return h("div", { className: NS + "-err" },
        h("strong", null, props.title || "出错了"),
        h("p", null, props.message),
        props.children || null);
    }

    // ---------------------------------------------------------------- 左栏对象列表
    function ObjectRow(props) {
      var o = props.object;
      return h("li", {
        className: NS + "-obj" + (props.active ? " " + NS + "-obj-active" : ""),
        onClick: props.onSelect,
        title: o.path,
      },
        h("span", { className: NS + "-obj-name" }, o.name),
        h("span", { className: NS + "-obj-meta" },
          h(NoteBadge, { notes: o.notes }),
          o.quiz_library && o.quiz_library.exists
            ? h(Tag, { tone: "info", title: o.quiz_library.path }, "题库")
            : h(Tag, { tone: "muted", title: "还没有 quiz-library 目录" }, "无题库"),
          o.git ? h(Tag, { tone: "muted", title: "本地 .git 发现 origin" }, "git") : null));
    }

    function Sidebar(props) {
      var [filter, setFilter] = useState("");
      var all = props.objects || [];
      var needle = filter.trim().toLowerCase();
      function match(o) { return !needle || o.name.toLowerCase().indexOf(needle) >= 0; }
      var courses = all.filter(function (o) { return o.kind === "course" && match(o); });
      var study = all.filter(function (o) { return o.kind === "study" && match(o); });

      function group(title, items, emptyHint) {
        return h("div", { className: NS + "-group" },
          h("div", { className: NS + "-group-head" },
            h("span", { className: NS + "-group-title" }, title),
            h("span", { className: NS + "-group-count" }, items.length)),
          items.length
            ? h("ul", { className: NS + "-objlist" }, items.map(function (o) {
                return h(ObjectRow, {
                  key: o.id, object: o, active: o.id === props.selected,
                  onSelect: function () { props.onSelect(o.id); },
                });
              }))
            : h("p", { className: NS + "-sidehint" }, emptyHint));
      }

      return h("aside", { className: NS + "-side" },
        h("div", { className: NS + "-side-head" },
          h("h2", { className: NS + "-side-title" }, "学习对象"),
          h("span", { className: NS + "-side-count" }, all.length)),
        h("input", {
          className: NS + "-search", placeholder: "过滤名称…", value: filter,
          onChange: function (e) { setFilter(e.target.value); },
        }),
        group("课程", courses, props.roots && props.roots.courses && !props.roots.courses.exists
          ? "课程根目录不存在：" + props.roots.courses.path
          : "没有匹配的课程目录"),
        group("自主学习", study, "没有匹配的自主学习主题（见后端 roots.study）"),
        h("p", { className: NS + "-sidehint" },
          "扫描只读一层，跳过 vault / 隐藏目录 / 符号链接。"));
    }

    // ---------------------------------------------------------------- 题目卡
    function SubmitBar(props) {
      var q = props.question;
      var g = props.result;
      if (!g) {
        return h("div", { className: NS + "-qactions" },
          h("button", {
            className: NS + "-btn " + NS + "-btn-primary",
            disabled: props.disabled, onClick: props.onSubmit,
          }, "提交"),
          props.hint ? h("span", { className: NS + "-qhint" }, props.hint) : null);
      }
      return h("div", { className: NS + "-qactions" },
        h("button", { className: NS + "-btn " + NS + "-btn-primary", onClick: props.onNext }, "下一题"),
        h("span", { className: NS + "-verdict " + (g.judged
          ? (g.correct ? NS + "-verdict-ok" : NS + "-verdict-bad")
          : NS + "-verdict-manual") },
          g.judged ? (g.correct ? "答对" : "答错") : "需自评"),
        h("span", { className: NS + "-qhint" }, typeof g.detail === "string" ? g.detail : ""));
    }

    function PerBlankDetail(props) {
      var rows = Array.isArray(props.detail) ? props.detail : [];
      if (!rows.length) return null;
      return h("ul", { className: NS + "-blankdetail" }, rows.map(function (r) {
        return h("li", { key: r.index, className: r.correct ? NS + "-ok" : NS + "-bad" },
          h("b", null, "第 " + r.index + " 空"),
          h("span", null, "你填：" + (r.user || "（空）")),
          h("span", null, "可接受：" + (r.accepted || []).join(" / ")));
      }));
    }

    function QuestionCard(props) {
      var q = props.question;
      var grade = props.grade;
      var [result, setResult] = useState(null);
      var [pick, setPick] = useState([]);      // single/multiple: 字母数组
      var [judgePick, setJudgePick] = useState(null);
      var [blanks, setBlanks] = useState([]);
      var [shortText, setShortText] = useState("");

      var qid = q && q.id;
      useEffect(function () {
        setResult(null);
        setPick([]);
        setJudgePick(null);
        setBlanks([]);
        setShortText("");
      }, [qid, props.cursor]);

      if (!q) {
        return h(Empty, { title: "这个筛选下没有题目", hint: "换一个题型或题库。" });
      }

      function submit() {
        var chosen;
        if (q.type === "single") chosen = pick;
        else if (q.type === "multiple") chosen = pick;
        else if (q.type === "judge") chosen = judgePick;
        else if (q.type === "blank") chosen = blanks;
        else chosen = [shortText];
        var outcome = grade(q, chosen);
        setResult(outcome);
        if (props.onAnswered) props.onAnswered(q, outcome, chosen);
      }

      var disabled = q.type === "single" || q.type === "multiple" ? !pick.length
        : q.type === "judge" ? !judgePick
        : q.type === "blank" ? !blanks.filter(Boolean).length
        : !shortText.trim();

      function toggle(key) {
        if (result) return;
        if (q.type === "single") { setPick([key]); return; }
        setPick(function (prev) {
          return prev.indexOf(key) >= 0 ? prev.filter(function (k) { return k !== key; })
                                        : prev.concat([key]);
        });
      }

      var correctSet = {};
      (q.answer || []).forEach(function (a) { correctSet[String(a).toUpperCase()] = 1; });

      var optionNodes = null;
      if (q.options && q.options.length) {
        optionNodes = h("div", { className: NS + "-opts", role: q.type === "multiple" ? "group" : "radiogroup" },
          q.options.map(function (opt) {
            var picked = pick.indexOf(opt.key) >= 0;
            var isRight = !!result && !!correctSet[opt.key];
            var isWrongPick = !!result && picked && !isRight;
            return h("button", {
              key: opt.key, type: "button", disabled: !!result,
              className: NS + "-opt" + (picked ? " " + NS + "-opt-picked" : "") +
                (isRight ? " " + NS + "-opt-right" : "") +
                (isWrongPick ? " " + NS + "-opt-wrong" : ""),
              "aria-checked": picked,
              onClick: function () { toggle(opt.key); },
            }, h("i", { className: NS + "-optkey" }, opt.key),
               h("span", { className: NS + "-opttext" }, opt.text));
          }));
      } else if (q.type === "judge") {
        optionNodes = h("div", { className: NS + "-opts" }, ["正确", "错误"].map(function (v) {
          var picked = judgePick === v;
          var isRight = !!result && (q.answer || [])[0] === v;
          return h("button", {
            key: v, type: "button", disabled: !!result,
            className: NS + "-opt" + (picked ? " " + NS + "-opt-picked" : "") +
              (isRight ? " " + NS + "-opt-right" : "") +
              (result && picked && !isRight ? " " + NS + "-opt-wrong" : ""),
            onClick: function () { if (!result) setJudgePick(v); },
          }, v);
        }));
      } else if (q.type === "blank") {
        var groups = q.blank_answers || [];
        optionNodes = h("div", { className: NS + "-blanks" }, groups.map(function (accepted, index) {
          return h("label", { key: index, className: NS + "-blankrow" },
            h("span", null, "第 " + (index + 1) + " 空"),
            h("input", {
              className: NS + "-input", disabled: !!result,
              value: blanks[index] || "",
              onChange: function (e) {
                var next = blanks.slice();
                next[index] = e.target.value;
                setBlanks(next);
              },
            }));
        }));
      } else {
        optionNodes = h("div", { className: NS + "-blanks" },
          h("textarea", {
            className: NS + "-input " + NS + "-textarea", rows: 4, disabled: !!result,
            placeholder: "写下你的回答（不会自动判分，提交后对照参考答案自评）",
            value: shortText, onChange: function (e) { setShortText(e.target.value); },
          }));
      }

      return h("article", { className: NS + "-qcard" },
        h("header", { className: NS + "-qhead" },
          h(Tag, { tone: "info" }, TYPE_LABEL[q.type] || q.type),
          h("span", { className: NS + "-qnum" },
            "第 " + (props.cursor + 1) + " / " + props.total + " 题"),
          props.stats ? h("span", { className: NS + "-qprog" },
            "已答 " + props.stats.answered + " · 正确 " + props.stats.correct +
            " · 错 " + props.stats.wrong) : null),
        h("p", { className: NS + "-stem" }, q.stem),
        optionNodes,
        h(SubmitBar, {
          question: q, result: result, disabled: disabled,
          onSubmit: submit, onNext: props.onNext,
          hint: q.type === "multiple" ? "多选：答案集合必须完全一致" : null,
        }),
        result ? h("div", { className: NS + "-explain" },
          h("p", { className: NS + "-answer" },
            h("strong", null, "参考答案："),
            q.type === "judge" ? (q.answer || []).join("")
              : q.type === "short" ? (q.answer || []).join("；")
              : q.type === "blank" ? (q.blank_answers || []).map(function (g) { return g[0]; }).join("；")
              : (q.answer || []).join("")),
          h(PerBlankDetail, { detail: result.detail }),
          q.explanation ? h("p", { className: NS + "-parsed" }, q.explanation)
                        : h("p", { className: NS + "-parsed " + NS + "-muted" }, "（题库未提供解析）"),
          q.source ? h("p", { className: NS + "-src" },
            "来源：" + q.source.path + (q.source.locator ? " · " + q.source.locator : "")) : null)
        : null);
    }

    return {
      TYPE_LABEL: TYPE_LABEL,
      Empty: Empty, Tag: Tag, NoteBadge: NoteBadge, Spinner: Spinner, Err: Err,
      Sidebar: Sidebar, ObjectRow: ObjectRow,
      QuestionCard: QuestionCard,
    };
  }

  root.WBL_VIEWS = { make: make, TYPE_LABEL: TYPE_LABEL };
})(typeof window !== "undefined" ? window : globalThis);
