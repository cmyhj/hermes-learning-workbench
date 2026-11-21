/*
 * wb-learn 前端 · 题库面板（选择题库 / 做题 / 无题库时的生成入口）
 *
 * 生成流程（关键：不伪造「已生成」）：
 *   1. 无题库时显示**生成入口按钮**，点击 → 把一段「基于真实材料与笔记生成标准题库」
 *      的提示词注入当前 Hermes agent 面板（bindPrompt），由真实 agent 产出 JSON；
 *   2. 用户把 agent 回复里的 JSON 贴回来（或点「从对话取最后一次回复」），
 *      前端 extractBank 抽出来 → POST /quiz/drafts 落草稿；
 *   3. 显示草稿校验结果（哪几题有问题、缺 source 的题）→ 用户点「确认保存」才写
 *      quiz-library；覆盖同名文件要再确认一次。
 *
 * 不做：自造题目、把没通过校验的草稿说成已保存、在等待期间显示假题目。
 */
(function (root) {
  "use strict";

  var NS = "wbl";

  function make(SDK, API, portions, QUIZ) {
    var React = SDK.React;
    var h = React.createElement;
    var useState = SDK.hooks.useState;
    var useEffect = SDK.hooks.useEffect;
    var Empty = portions.Empty, Tag = portions.Tag, Spinner = portions.Spinner, Err = portions.Err;
    var QuestionCard = portions.QuestionCard;
    var TYPE_LABEL = portions.TYPE_LABEL;
    var msg = API.messageOf;

    var FILTERS = ["all", "single", "multiple", "judge", "blank", "short"];

    // ---------------------------------------------------------------- 生成面板
    function GeneratePanel(props) {
      var [text, setText] = useState("");
      var [draft, setDraft] = useState(null);
      var [error, setError] = useState(null);
      var [busy, setBusy] = useState(false);
      var [saved, setSaved] = useState(null);
      var [filename, setFilename] = useState("");
      var [confirmOverwrite, setConfirmOverwrite] = useState(false);

      function buildPrompt() {
        var obj = props.object || {};
        var notes = obj.notes || {};
        return [
          "请基于【真实材料】为本学习对象生成标准题库草稿，不要凭空编题。",
          "",
          "对象：" + obj.name + "（" + obj.kind + "）",
          "材料根目录：" + obj.path,
          notes.linked ? "Obsidian 笔记目录：" + notes.vault_path : "（当前对象没有关联笔记目录）",
          "题库目标目录：" + (obj.quiz_library ? obj.quiz_library.path : "（尚未创建）"),
          "",
          "硬要求：",
          "1. 每一题都必须能从材料或笔记原文得到依据，禁止编造数字、术语、作者、页码；",
          "2. 每题带 source：{path: 相对材料根的路径, locator: 页码或小节}；无法定位就删掉该题；",
          "3. type 只允许 single / multiple / judge / blank / short；",
          "4. single/multiple 的 answer 用选项字母数组；judge 用 [\"正确\"] 或 [\"错误\"]；",
          "5. blank 用 blank_answers: [[\"主答案\",\"等价写法\"]...]，每空一组；",
          "6. short 只写材料里已有的参考答案，不生成新结论；",
          "7. explanation 只允许来自原文，没有就留空；",
          "8. 输出一个 ```json 代码块，形如 {\"name\":\"...\",\"questions\":[...]}。",
          "",
          "你可以直接读取上面的材料路径（只读）。生成后把 JSON 贴回本面板的「粘贴 JSON」框。",
        ].join("\n");
      }

      function askAgent() {
        if (!props.onBindPrompt) { setError("当前没有可用的 agent 面板"); return; }
        var ok = props.onBindPrompt(buildPrompt());
        setError(ok ? null : "agent 面板还没连上：先发一条消息建会话，再点一次生成。");
      }

      function submit() {
        var bank = QUIZ.extractBank(text);
        if (!bank) { setError("这段文本里没有可解析的题库 JSON（需要 {questions:[...]} 或数组）。"); return; }
        setBusy(true); setError(null); setSaved(null);
        API.stageDraft(props.objectId, bank.name || null, bank)
          .then(function (d) {
            setDraft(d);
            setFilename(d.suggested_file || "");
            setConfirmOverwrite(false);
          })
          .catch(function (e) { setError(msg(e)); })
          .then(function () { setBusy(false); });
      }

      function commit() {
        if (!draft) return;
        setBusy(true); setError(null);
        API.commitDraft(props.objectId, draft.draft_id, filename, confirmOverwrite)
          .then(function (r) {
            setSaved(r);
            setDraft(null);
            setText("");
            if (props.onSaved) props.onSaved();
          })
          .catch(function (e) {
            var m = msg(e);
            if (/已存在同名题库/.test(m)) { setConfirmOverwrite(true); setError(m + " —— 再点一次「覆盖保存」确认。"); }
            else setError(m);
          })
          .then(function () { setBusy(false); });
      }

      return h("div", { className: NS + "-gen" },
        h("div", { className: NS + "-gencard" },
          h("h3", { className: NS + "-gentitle" }, "生成题库（交给当前 Hermes agent）"),
          h("p", { className: NS + "-sidehint" },
            "本面板不自己出题：它把「基于真实材料与笔记生成标准题库」的要求交给你正在用的 agent，" +
            "由 agent 读材料后产出 JSON。生成结果先做草稿校验，确认后才写入 quiz-library。"),
          h("div", { className: NS + "-genactions" },
            h("button", { className: NS + "-btn " + NS + "-btn-primary", onClick: askAgent },
              "把生成要求发给 agent"),
            h("button", {
              className: NS + "-btn " + NS + "-btn-quiet",
              onClick: function () {
                if (props.onPullReply) {
                  var got = props.onPullReply();
                  if (got) { setText(got); setError(null); }
                  else setError("agent 面板里还没有回复可提取。");
                } else setError("没有可用的 agent 面板。");
              },
            }, "取 agent 最后一条回复")),
          h("label", { className: NS + "-genlabel" }, "粘贴 agent 返回的题库 JSON"),
          h("textarea", {
            className: NS + "-input " + NS + "-textarea " + NS + "-geninput",
            rows: 8, value: text, placeholder: "{\"name\":\"第3章 相机标定\",\"questions\":[ ... ]}",
            onChange: function (e) { setText(e.target.value); },
          }),
          h("button", {
            className: NS + "-btn " + NS + "-btn-primary",
            disabled: busy || !text.trim(), onClick: submit,
          }, busy ? "校验中…" : "校验并生成草稿"),
          error ? h("p", { className: NS + "-err" }, error) : null,
          saved ? h("p", { className: NS + "-saved" },
            "已保存：" + saved.file + "（" + saved.total + " 题）→ " + saved.rel_path) : null),

        draft ? h("div", { className: NS + "-gencard" },
          h("h3", { className: NS + "-gentitle" }, "草稿校验结果"),
          h("p", null,
            h(Tag, { tone: draft.ok ? "ok" : "warn" }, draft.ok ? "校验通过" : "有 " + draft.issues.length + " 个问题"),
            " 共 " + draft.total + " 题 · " +
            Object.keys(draft.counts || {}).filter(function (k) { return draft.counts[k]; })
              .map(function (k) { return TYPE_LABEL[k] + " " + draft.counts[k]; }).join(" / ")),
          draft.issues && draft.issues.length
            ? h("ul", { className: NS + "-issues" }, draft.issues.slice(0, 20).map(function (s, i) {
                return h("li", { key: i }, s);
              }))
            : null,
          draft.warnings && draft.warnings.length
            ? h("ul", { className: NS + "-issues " + NS + "-warn" }, draft.warnings.slice(0, 10).map(function (s, i) {
                return h("li", { key: i }, s);
              }))
            : null,
          h("div", { className: NS + "-genactions" },
            h("label", { className: NS + "-genlabel" }, "保存文件名"),
            h("input", {
              className: NS + "-input", value: filename,
              onChange: function (e) { setFilename(e.target.value); },
            })),
          h("p", { className: NS + "-sidehint" },
            "保存位置：" + (props.object && props.object.quiz_library ? props.object.quiz_library.path : "quiz-library") +
            "（这是唯一允许写入题库的目录）"),
          h("button", {
            className: NS + "-btn " + (confirmOverwrite ? NS + "-btn-danger" : NS + "-btn-primary"),
            disabled: busy || !draft.ok, onClick: commit,
          }, confirmOverwrite ? "覆盖保存（已确认）" : "确认保存到 quiz-library"),
          !draft.ok ? h("p", { className: NS + "-sidehint" },
            "有问题的草稿不允许保存：请让 agent 修正后重新粘贴。") : null)
          : null,

        h("div", { className: NS + "-gencard" },
          h("h3", { className: NS + "-gentitle" }, "草稿预览"),
          draft && draft.preview && draft.preview.length
            ? h("ol", { className: NS + "-previewlist" }, draft.preview.slice(0, 10).map(function (q) {
                return h("li", { key: q.id },
                  h(Tag, { tone: "info" }, TYPE_LABEL[q.type] || q.type),
                  h("span", null, q.stem),
                  q.source ? h("span", { className: NS + "-src" },
                    " · " + q.source.path + (q.source.locator ? " " + q.source.locator : "")) : null);
              }))
            : h("p", { className: NS + "-sidehint" }, "还没有草稿：先让 agent 生成，再在这里看预览。")));
    }

    // ---------------------------------------------------------------- 做题
    function QuizPanel(props) {
      var [banks, setBanks] = useState(null);
      var [error, setError] = useState(null);
      var [bankId, setBankId] = useState(null);
      var [bank, setBank] = useState(null);
      var [bankError, setBankError] = useState(null);
      var [filter, setFilter] = useState("all");
      var [cursor, setCursor] = useState(0);
      var [progress, setProgress] = useState({ answered: {}, cursor: 0 });
      var [mode, setMode] = useState("quiz");   // quiz | generate

      useEffect(function () {
        var alive = true;
        setBanks(null); setError(null); setBank(null); setBankId(null); setMode("quiz");
        API.banks(props.objectId)
          .then(function (d) {
            if (!alive) return;
            setBanks(d);
            if (d.banks && d.banks.length) {
              var remembered = null;
              try { remembered = root.localStorage.getItem("wbl.bank." + props.objectId); } catch (e) {}
              var hit = remembered && d.banks.some(function (b) { return b.id === remembered; });
              setBankId(hit ? remembered : d.banks[0].id);
            } else {
              setMode("generate");
            }
          })
          .catch(function (e) { if (alive) setError(msg(e)); });
        return function () { alive = false; };
      }, [props.objectId, props.reloadToken]);

      useEffect(function () {
        if (!bankId) return;
        var alive = true;
        setBank(null); setBankError(null); setCursor(0);
        API.bank(props.objectId, bankId, null, 500)
          .then(function (d) {
            if (!alive) return;
            setBank(d);
            setProgress(QUIZ.getScope(props.objectId, bankId));
            setCursor(QUIZ.getScope(props.objectId, bankId).cursor || 0);
            if (d.issues && d.issues.length) setBankError("题库校验提示：" + d.issues.slice(0, 3).join("；"));
          })
          .catch(function (e) { if (alive) setBankError(msg(e)); });
        return function () { alive = false; };
      }, [props.objectId, bankId, props.reloadToken]);

      function pickBank(id) {
        setBankId(id);
        try { root.localStorage.setItem("wbl.bank." + props.objectId, id); } catch (e) {}
      }

      var questions = (bank && bank.questions) || [];
      var visible = filter === "all" ? questions
        : questions.filter(function (q) { return q.type === filter; });
      var current = visible.length ? visible[Math.min(cursor, visible.length - 1)] : null;
      var stats = QUIZ.stats(progress.answered);

      function onAnswered(q, outcome) {
        if (!outcome.judged) return;   // 简答不自评就不记进正确率
        var answered = Object.assign({}, progress.answered);
        answered[q.id] = { correct: !!outcome.correct, at: new Date().toISOString() };
        setProgress(QUIZ.putProgress(props.objectId, bankId, { answered: answered, cursor: cursor }));
      }

      function next() {
        var to = Math.min(cursor + 1, Math.max(0, visible.length - 1));
        setCursor(to);
        QUIZ.putProgress(props.objectId, bankId, { answered: progress.answered, cursor: to });
      }

      if (error) return h(Err, { title: "题库列表读取失败", message: error });
      if (!banks) return h(Spinner, { label: "读取题库…" });

      var tabs = h("div", { className: NS + "-banktabs" },
        (banks.banks || []).map(function (b) {
          return h("button", {
            key: b.id, className: NS + "-banktab" + (b.id === bankId ? " " + NS + "-banktab-active" : ""),
            onClick: function () { pickBank(b.id); setMode("quiz"); }, title: b.rel_path,
          },
            h("span", null, b.name || b.id),
            h("small", null, (b.total || 0) + " 题" + (b.read_only ? " · 只读" : "")));
        }),
        h("button", {
          className: NS + "-banktab " + NS + "-banktab-add",
          onClick: function () { setMode(mode === "generate" ? "quiz" : "generate"); },
          title: "用当前 agent 基于真实材料生成新题库草稿",
        }, mode === "generate" ? "返回做题" : "+ 生成题库"));

      if (mode === "generate") {
        return h("div", null, tabs, h(GeneratePanel, {
          objectId: props.objectId, object: props.object,
          onBindPrompt: props.onBindPrompt, onPullReply: props.onPullReply,
          onSaved: props.onSaved,
        }));
      }

      if (!banks.banks || !banks.banks.length) {
        return h("div", null, tabs,
          h(Empty, {
            title: "这个对象还没有题库",
            hint: "quiz-library 固定路径：" + (banks.quiz_library ? banks.quiz_library.path : "") +
                  "。可以点「+ 生成题库」，让当前 Hermes agent 基于真实材料与笔记生成草稿。",
          }));
      }

      return h("div", null, tabs,
        bankError ? h("p", { className: NS + "-err" }, bankError) : null,
        h("div", { className: NS + "-quizmeta" },
          h("div", { className: NS + "-kinds" }, FILTERS.map(function (k) {
            var n = k === "all" ? questions.length
              : questions.filter(function (q) { return q.type === k; }).length;
            return h("button", {
              key: k, disabled: !n,
              className: NS + "-kind" + (filter === k ? " " + NS + "-kind-active" : ""),
              onClick: function () { setFilter(k); setCursor(0); },
            }, (k === "all" ? "全部" : TYPE_LABEL[k]) + " " + n);
          })),
          h("span", { className: NS + "-qprog" },
            "已答 " + stats.answered + " · 正确 " + stats.correct + " · 错 " + stats.wrong),
          bank && bank.bank ? h(Tag, { tone: bank.bank.ok ? "ok" : "warn" },
            bank.bank.format === "text" ? "既有 txt（只读）" : "标准 JSON") : null),
        bank
          ? h(QuestionCard, {
              question: current, cursor: Math.min(cursor, Math.max(0, visible.length - 1)),
              total: visible.length, stats: stats, grade: QUIZ.grade,
              onAnswered: onAnswered, onNext: next,
            })
          : h(Spinner, { label: "读取题目…" }),
        bank && bank.bank && bank.bank.generated
          ? h("p", { className: NS + "-sidehint" },
              "来源：生成于 " + (bank.bank.generated.at || "?") + " · " +
              (bank.bank.generated.by || "hermes-agent") +
              "（题库为机器生成草稿经人工确认，仅供参考）")
          : null);
    }

    return { QuizPanel: QuizPanel, GeneratePanel: GeneratePanel };
  }

  root.WBL_QUIZPANEL = { make: make };
})(typeof window !== "undefined" ? window : globalThis);
