/*
 * wb-learn 前端 · 自主学习子条目面板
 *
 * 一个 study: 对象下多个子条目，每个子条目含多媒体项：
 *   zotero（论文，点击 → zotero://select/library/items/<key>）
 *   repo（仓库：已下载 → /study/items/open 用 VSCode 打开；未下载 → window.open 网址）
 *   videos / websites（window.open）
 *
 * 点击任何 item 都先调 /study/items/click 记一次，再执行动作；
 * 计数失败不回滚动作、但必须提示（计数与动作并发，失败不互相阻塞）。
 *
 * 找仓库流程：选中论文 → 「找代码仓库」→ 候选列表（stars/描述/来源）→
 * 用户点选 → 「下载到 code/<owner>__<repo>」→ 显示克隆结果与最终路径。
 * 绝不静默自动下载。
 *
 * 纯 React.createElement，无 JSX / 无 import。样式走 --color-* 主题变量。
 */
(function (root) {
  "use strict";

  var NS = "wbl";

  // 前端 URL 形状校验（后端仍会再校验一次，这里只为即时反馈）
  var GH_RE = /^https:\/\/github\.com\/[A-Za-z0-9][\w.-]{0,38}\/[A-Za-z0-9][\w.-]{0,100}\/?$/;
  var HF_RE = /^https:\/\/huggingface\.co\/[A-Za-z0-9][\w.-]{0,38}\/[A-Za-z0-9][\w.-]{0,100}$/;
  var LINK_RE = /^https?:\/\/[^\s/]+/;
  var KEY_RE = /^[A-Z0-9]{8}$/;

  function isGithub(url) { return GH_RE.test((url || "").trim()); }
  function isHf(url) { return HF_RE.test((url || "").trim()); }
  function isLink(url) { return LINK_RE.test((url || "").trim()); }

  // 仓库目标路径的展示（后端决定真实路径，这里只用于按钮文案）
  function repoTargetLabel(provider, url) {
    var m = (url || "").match(/https:\/\/(?:github\.com|huggingface\.co)\/([^/]+)\/([^/]+)\/?$/);
    if (!m) return "code/<owner>__<repo>";
    return "code/" + m[1] + "__" + m[2];
  }

  function make(SDK, API, portions) {
    var React = SDK.React;
    var h = React.createElement;
    var useState = SDK.hooks.useState;
    var useEffect = SDK.hooks.useEffect;
    var useCallback = SDK.hooks.useCallback;
    var Empty = portions.Empty, Tag = portions.Tag, Spinner = portions.Spinner, Err = portions.Err;
    var msg = API.messageOf;

    // 量面板自身宽度，决定内部两列还是堆叠
    function usePanelWidth(ref) {
      var [w, setW] = useState(0);
      useEffect(function () {
        if (!ref.current) return;
        var el = ref.current;
        function measure() { setW(el.getBoundingClientRect().width); }
        measure();
        if (typeof ResizeObserver === "undefined") {
          window.addEventListener("resize", measure);
          return function () { window.removeEventListener("resize", measure); };
        }
        var ro = new ResizeObserver(measure);
        ro.observe(el);
        return function () { ro.disconnect(); };
      }, []);
      return w;
    }

    // ---------------------------------------------------------------- item 点击
    function clickThenAct(objectId, item, entry, action, onError, onTracked) {
      // 先发记录请求，但绝不等待它才导航；两边的失败各自显示。
      try {
        Promise.resolve(API.clickItem(objectId, item.id, item.type, entry ? entry.id : ""))
          .then(function () { if (onTracked) onTracked(); })
          .catch(function (e) { if (onError) onError("点击记录失败（仍继续打开）：" + msg(e)); });
      } catch (e) {
        if (onError) onError("点击记录失败（仍继续打开）：" + msg(e));
      }
      try { action(); }
      catch (e) { if (onError) onError("打开失败：" + msg(e)); }
    }

    // ---------------------------------------------------------------- 单项行
    function ItemRow(props) {
      var item = props.item, entry = props.entry, objectId = props.objectId;
      var rec = props.record || {};
      var [busy, setBusy] = useState(false);
      var [openErr, setOpenErr] = useState(null);
      var [reading, setReading] = useState(false);

      // TextFold折叠阅读：只读元数据 → TextFold 折读；拿到 pid 就打开，生成在后台继续
      function sendReading() {
        if (reading) return;
        setReading(true); setOpenErr(null);
        API.readPaper(item.itemKey, "zh")
          .then(function (res) {
            if (!res || !res.ok) { setReading(false); setOpenErr((res && res.error) || "TextFold折叠阅读失败"); return; }
            var job = res.job || {}, pid = res.paper || job.pid;
            var openReader = function () {
              window.open((function(){var h=location.hostname;return (h==="127.0.0.1"||h==="localhost")?"http://127.0.0.1:3017":"http://"+h+":3018";})() + "/p/" + encodeURIComponent(pid) + "?lang=zh",
                "_blank", "noopener");
            };
            if (pid) { openReader(); setReading(false); }
            if (!job.id) { if (!pid) setReading(false); return; }
            var tick = function () {
              API.readJob(job.id).then(function (r) {
                if (!r || !r.ok) { setTimeout(tick, 2000); return; }
                if (r.job.status === "done") { if (!pid) openReader(); setReading(false); }
                else if (r.job.status === "error") { setReading(false); setOpenErr("生成失败：" + (r.job.error || "")); }
                else setTimeout(tick, 2000);
              }).catch(function () { setTimeout(tick, 2000); });
            };
            tick();
          })
          .catch(function (e) { setReading(false); setOpenErr(msg(e)); });
      }
      function track(action) {
        setOpenErr(null);
        clickThenAct(objectId, item, entry, action,
          function (error) { setOpenErr(error); }, props.onTracked);
      }

      function openPaper() {
        track(function () {
          window.open("zotero://select/library/items/" + item.itemKey, "_blank");
        });
      }
      function openRepo() {
        if (item.local_path) {
          setBusy(true); setOpenErr(null);
          track(function () {
            API.openRepo(objectId, { item_id: item.id })
              .then(function (r) {
                if (r && r.status !== "ok") setOpenErr((r && r.error) || "打开失败");
              })
              .catch(function (e) { setOpenErr(msg(e)); })
              .then(function () { setBusy(false); });
          });
        } else {
          track(function () { window.open(item.url, "_blank", "noopener,noreferrer"); });
        }
      }
      function openLink() {
        track(function () { window.open(item.url, "_blank", "noopener,noreferrer"); });
      }

      var label, onClick, busyTag;
      if (item.type === "zotero") {
        label = item.title || item.itemKey;
        onClick = openPaper;
        busyTag = h(Tag, { tone: "info" }, "论文");
      } else if (item.type === "repo") {
        label = (item.owner || "") + "/" + (item.repo || item.url);
        onClick = openRepo;
        busyTag = h(Tag, { tone: item.local_path ? "ok" : "muted" },
          item.local_path ? "已下载" : "未下载");
      } else if (item.type === "videos") {
        label = item.title || item.url;
        onClick = openLink;
        busyTag = h(Tag, { tone: "info" }, "视频");
      } else {
        label = item.title || item.url;
        onClick = openLink;
        busyTag = h(Tag, { tone: "info" }, "网站");
      }

      return h("li", { className: NS + "-item" },
        h("div", { className: NS + "-item-main" },
          busyTag,
          h("span", { className: NS + "-item-name", title: label }, label),
            h("span", { className: NS + "-item-meta" },
            "点击 " + (rec.count || 0) +
            (rec.last_at ? " · 最近 " + String(rec.last_at).slice(5, 16).replace("T", " ") : "")),
        h("div", { className: NS + "-item-act" },
          item.type === "zotero" ? h("button", {
            className: NS + "-btn " + NS + "-btn-quiet",
            disabled: reading,
            title: "交给 TextFold 按 1–5 级折读",
            onClick: sendReading,
          }, reading ? "折叠中…" : "TextFold折叠阅读") : null,
          h("button", {
            className: NS + "-btn " + NS + "-btn-quiet", disabled: busy, onClick: onClick,
          }, item.type === "repo" && item.local_path ? "VSCode 打开" : "打开"),
          props.onDelete ? h("button", {
            className: NS + "-btn " + NS + "-btn-quiet", onClick: props.onDelete,
          }, "删除") : null)),
        openErr ? h("p", { className: NS + "-err" }, openErr) : null);
    }

    // ---------------------------------------------------------------- 找仓库流程
    function FindRepoCard(props) {
      var objectId = props.objectId, zoteroItem = props.zoteroItem, entry = props.entry;
      var [phase, setPhase] = useState("idle"); // idle | searching | candidates | cloning | done
      var [candidates, setCandidates] = useState([]);
      var [picked, setPicked] = useState(null);
      var [result, setResult] = useState(null);
      var [error, setError] = useState(null);
      var [note, setNote] = useState(null);

      function search() {
        setPhase("searching"); setError(null); setResult(null); setPicked(null); setNote(null);
        API.resolveRepo(zoteroItem.itemKey)
          .then(function (d) {
            if (d.status === "not_found" || (d.candidates && !d.candidates.length)) {
              setError("没有找到候选仓库。已尝试查询：" + ((d.queries || []).join(" / ") || "（无）"));
              setPhase("idle");
              return;
            }
            if (d.status === "error") { setError(d.error || "搜索失败"); setPhase("idle"); return; }
            setCandidates(d.candidates || []);
            setPhase("candidates");
          })
          .catch(function (e) { setError(msg(e)); setPhase("idle"); });
      }

      function clone() {
        if (!picked) return;
        var provider = picked.provider || (picked.url.indexOf("huggingface") >= 0 ? "huggingface" : "github");
        if ((entry.items || []).some(function (it) { return it.type === "repo" && it.provider === provider; })) {
          setError("此子条目已有一个 " + (provider === "github" ? "GitHub" : "Hugging Face") + " 仓库；先删除它再下载候选。");
          return;
        }
        if (!window.confirm("确认将 " + picked.url + " 克隆到此 study 项目的 code/ 目录？")) return;
        setPhase("cloning"); setError(null); setNote(null);
        API.cloneRepo(objectId, {
          provider: picked.provider || (picked.url.indexOf("huggingface") >= 0 ? "huggingface" : "github"),
          url: picked.url, entry_id: entry.id,
        })
          .then(function (r) {
            if (r.status === "ok") {
              setResult(r);
              setPhase("done");
              // 克隆成功后把 repo 项加进子条目（这样它出现在列表里、可 VSCode 打开）
              API.saveStudyEntry(objectId, {
                entry_id: entry.id,
                add_items: [{
                  type: "repo", provider: provider, url: picked.url,
                  local_path: r.path, cloned_at: r.cloned_at,
                }],
              })
                .then(function () { if (props.onDone) props.onDone(); })
                .catch(function (e) { setNote("仓库已下载，但加入子条目失败：" + msg(e)); });
            } else if (r.status === "exists") {
              setResult(r); setPhase("done");
            } else {
              setError((r && r.error) || "克隆失败"); setPhase("candidates");
            }
          })
          .catch(function (e) { setError(msg(e)); setPhase("candidates"); });
      }

      var body;
      if (phase === "searching") body = h(Spinner, { label: "搜索仓库候选…" });
      else if (phase === "candidates") {
        body = h("div", { className: NS + "-findrepo" },
          h("p", { className: NS + "-sidehint" },
            "选择要下载的候选（绝不自动下载）。目标：code/ 目录。"),
          h("ul", { className: NS + "-candlist" }, candidates.map(function (c, i) {
            return h("li", {
              key: c.full_name || i,
              className: NS + "-cand" + (picked && picked.url === c.url ? " " + NS + "-cand-pick" : ""),
              onClick: function () { setPicked(c); },
            },
              h("span", { className: NS + "-cand-name" }, c.full_name || (c.owner + "/" + c.repo)),
              h("span", { className: NS + "-filemeta" },
                "★ " + (c.stars == null ? "?" : c.stars) +
                (c.source ? " · " + (c.source === "zotero-url" ? "论文链接" : "搜索") : "")),
              c.description ? h("span", { className: NS + "-cand-desc" }, c.description) : null);
          })),
          h("div", { className: NS + "-linkrow" },
            h("button", {
              className: NS + "-btn " + NS + "-btn-primary", disabled: !picked, onClick: clone,
            }, "下载到 " + (picked ? repoTargetLabel(picked.provider, picked.url) : "…"))));
      } else if (phase === "cloning") body = h(Spinner, { label: "克隆中（--depth 1）…" });
      else if (phase === "done") {
        body = h("div", { className: NS + "-findrepo" },
          result.status === "exists"
            ? h("p", { className: NS + "-notice" }, "目标已存在，未覆盖：" + result.path)
            : h("p", { className: NS + "-notice" }, "克隆成功：" + result.path),
          h("p", { className: NS + "-path" }, result.path),
          h("button", { className: NS + "-btn " + NS + "-btn-quiet", onClick: function () {
            setPhase("candidates"); setResult(null); setPicked(null);
          } }, "再选一个"));
      } else {
        body = h("div", { className: NS + "-linkrow" },
          h("button", { className: NS + "-btn " + NS + "-btn-quiet", onClick: search }, "找代码仓库"));
      }

      return h("div", { className: NS + "-findcard" },
        h("header", { className: NS + "-linkhead" },
          h("strong", null, "从论文找代码仓库"),
          h(Tag, { tone: "muted" }, zoteroItem.itemKey),
          h(Tag, { tone: "warn" }, "需人工确认")),
        error ? h("p", { className: NS + "-err" }, error) : null,
        note ? h("p", { className: NS + "-sidehint" }, note) : null,
        body);
    }

    // ---------------------------------------------------------------- 添加 item 表单
    function AddItemForm(props) {
      var objectId = props.objectId, entry = props.entry;
      var [kind, setKind] = useState("zotero");
      var [zoteroItems, setZoteroItems] = useState([]);
      var [zoteroCollection, setZoteroCollection] = useState("");
      var [zoteroCollectionKey, setZoteroCollectionKey] = useState("");
      var [zoteroChoices, setZoteroChoices] = useState([]);
      var [key, setKey] = useState("");
      var [title, setTitle] = useState("");
      var [url, setUrl] = useState("");
      var [provider, setProvider] = useState("github");
      var [err, setErr] = useState(null);
      var [busy, setBusy] = useState(false);

      // 论文选择只来自显式登记或项目同名的 Zotero collection。
      useEffect(function () {
        setZoteroItems([]); setKey(""); setZoteroCollection("");
        setZoteroCollectionKey(""); setZoteroChoices([]);
        setErr(null);
        API.links(objectId)
          .then(function (d) {
            var z = (d && d.zotero) || {}, resolution = z.resolution || {};
            var choices = resolution.choices || [];
            setZoteroChoices(choices);
            var selected = resolution.collection;
            if (selected) setZoteroCollectionKey(selected.key);
            else if (resolution.status === "ambiguous") return;
            else { setErr(resolution.error || "请先在 Zotero 设置中登记 collection"); return; }
            setZoteroCollection(selected.name + " [" + selected.key + "]");
            return API.zoteroCollection(selected.key).then(function (collection) {
              setZoteroItems(collection.items || []);
            });
          }).catch(function (e) { setErr("读取 Zotero collection 失败：" + msg(e)); });
      }, [objectId]);

      function selectZoteroCollection(k) {
        setZoteroCollectionKey(k); setKey(""); setZoteroItems([]);
        if (!k) return;
        API.zoteroCollection(k).then(function (collection) {
          setZoteroItems(collection.items || []);
          var c = zoteroChoices.filter(function (x) { return x.key === k; })[0];
          setZoteroCollection(c ? c.name + " [" + c.key + "]" : k);
        }).catch(function (e) { setErr("读取 Zotero collection 失败：" + msg(e)); });
      }

      function reset() { setKey(""); setTitle(""); setUrl(""); setErr(null); }

      function submit() {
        setErr(null);
        var item;
        if (kind === "zotero") {
          if ((entry.items || []).some(function (it) { return it.type === "zotero"; })) {
            setErr("每个子条目最多一个 Zotero 论文；请先删除已有论文"); return;
          }
          var k = key.trim().toUpperCase();
          if (!zoteroCollectionKey) { setErr("请先选择有效的 Zotero collection"); return; }
          if (!KEY_RE.test(k) || !zoteroItems.some(function (it) { return it.key === k; })) {
            setErr("请从当前配置的 Zotero collection 中选择论文"); return;
          }
          item = { type: "zotero", itemKey: k };
          if (zoteroChoices.length > 1 && !((zoteroChoices.length === 1) && zoteroChoices[0].key === ((props.links || {}).configured_key))) item.collectionKey = zoteroCollectionKey;
          if (title.trim()) item.title = title.trim();
        } else if (kind === "repo") {
          if ((entry.items || []).some(function (it) { return it.type === "repo" && it.provider === provider; })) {
            setErr("每个子条目最多一个 " + (provider === "github" ? "GitHub" : "Hugging Face") + " 仓库；请先删除已有仓库"); return;
          }
          var u = url.trim();
          if (provider === "github" && !isGithub(u)) { setErr("GitHub 仓库需 https://github.com/<owner>/<repo>"); return; }
          if (provider === "huggingface" && !isHf(u)) { setErr("HF 仓库需 https://huggingface.co/<owner>/<repo>"); return; }
          item = { type: "repo", provider: provider, url: u };
        } else {
          var lu = url.trim();
          if (!isLink(lu)) { setErr("链接需 http/https 开头且有 host"); return; }
          item = { type: kind, url: lu };
          if (title.trim()) item.title = title.trim();
        }
        setBusy(true);
        API.saveStudyEntry(objectId, { entry_id: entry.id, add_items: [item] })
          .then(function () { reset(); if (props.onDone) props.onDone(); })
          .catch(function (e) { setErr(msg(e)); })
          .then(function () { setBusy(false); });
      }

      var fields;
      if (kind === "zotero") {
        fields = h("div", { className: NS + "-form" },
          zoteroChoices.length > 1 ? h("label", null, "同名 collection 有多个，请选择路径") : null,
          zoteroChoices.length > 1 ? h("select", {
            className: NS + "-select", value: zoteroCollectionKey,
            onChange: function (e) { selectZoteroCollection(e.target.value); },
          }, h("option", { value: "" }, "（选择 collection）"), zoteroChoices.map(function (c) {
            var parent = c.parent && zoteroChoices.filter(function (x) { return x.key === c.parent; })[0];
            return h("option", { key: c.key, value: c.key }, (c.path || (parent ? parent.name + " / " : "") + c.name) + " [" + c.key + "]");
          })) : null,
          h("label", null, "当前 collection " + (zoteroCollection || "（未找到；请配置 collection）")),
          h("select", {
            className: NS + "-select", value: key,
            onChange: function (e) { setKey(e.target.value); },
          },
            h("option", { value: "" }, zoteroCollection ? "（选择论文）" : "（先配置 collection）"),
            zoteroItems.map(function (it) {
              return h("option", { key: it.key, value: it.key }, it.title + "  [" + it.key + "]");
            })),
          h("label", null, "标题（可选）"),
          h("input", {
            className: NS + "-input", value: title,
            onChange: function (e) { setTitle(e.target.value); },
          }));
      } else if (kind === "repo") {
        fields = h("div", { className: NS + "-form" },
          h("label", null, "provider"),
          h("select", {
            className: NS + "-select", value: provider,
            onChange: function (e) { setProvider(e.target.value); },
          },
            h("option", { value: "github" }, "github"),
            h("option", { value: "huggingface" }, "huggingface")),
          h("label", null, "仓库 URL"),
          h("input", {
            className: NS + "-input", value: url,
            placeholder: provider === "github" ? "https://github.com/<owner>/<repo>"
                                              : "https://huggingface.co/<owner>/<repo>",
            onChange: function (e) { setUrl(e.target.value); },
          }));
      } else {
        fields = h("div", { className: NS + "-form" },
          h("label", null, "链接 URL"),
          h("input", {
            className: NS + "-input", value: url,
            placeholder: "https://…",
            onChange: function (e) { setUrl(e.target.value); },
          }),
          h("label", null, "标题（可选）"),
          h("input", {
            className: NS + "-input", value: title,
            onChange: function (e) { setTitle(e.target.value); },
          }));
      }

      return h("div", { className: NS + "-additem" },
        h("div", { className: NS + "-linkrow" },
          h("select", {
            className: NS + "-select", value: kind,
            onChange: function (e) { setKind(e.target.value); reset(); },
          },
            h("option", { value: "zotero" }, "论文（Zotero）"),
            h("option", { value: "repo" }, "代码仓库"),
            h("option", { value: "videos" }, "视频"),
            h("option", { value: "websites" }, "网站")),
          h("button", {
            className: NS + "-btn " + NS + "-btn-quiet", disabled: busy, onClick: submit,
          }, busy ? "添加中…" : "添加"),
        ),
        h("div", { className: NS + "-form-body" }, fields),
        err ? h("p", { className: NS + "-err" }, err) : null);
    }

    // ---------------------------------------------------------------- 子条目详情
    function EntryDetail(props) {
      var entry = props.entry, objectId = props.objectId;
      var records = props.records || {};
      var [expanded, setExpanded] = useState(true);
      var [findFor, setFindFor] = useState(null); // {itemId, itemKey}
      var [note, setNote] = useState(null);

      function delItem(itemId) {
        API.deleteStudyEntry(objectId, entry.id, itemId)
          .then(function () { if (props.onDone) props.onDone(); })
          .catch(function (e) { setNote("删除失败：" + msg(e)); });
      }
      function delEntry() {
        if (!window.confirm("确认删除子条目「" + entry.title + "」及其所有项？")) return;
        API.deleteStudyEntry(objectId, entry.id)
          .then(function () { if (props.onDone) props.onDone(); })
          .catch(function (e) { setNote("删除失败：" + msg(e)); });
      }

      var items = entry.items || [];
      var zoteroItems = items.filter(function (it) { return it.type === "zotero"; });

      return h("section", { className: NS + "-entrycard" },
        h("header", { className: NS + "-linkhead" },
          h("button", {
            className: NS + "-link2", onClick: function () { setExpanded(!expanded); },
          }, expanded ? "▾" : "▸"),
          h("strong", null, entry.title),
          h(Tag, { tone: "muted" }, (items.length || 0) + " 项"),
          entry.note ? h("span", { className: NS + "-item-meta" }, entry.note) : null,
          h("button", {
            className: NS + "-btn " + NS + "-btn-quiet", onClick: delEntry,
          }, "删除条目")),
        note ? h("p", { className: NS + "-sidehint" }, note) : null,
        expanded ? h("div", { className: NS + "-entrybody" },
          items.length
            ? h("ul", { className: NS + "-itemlist" }, items.map(function (it) {
                return h(ItemRow, {
                  key: it.id, item: it, entry: entry, objectId: objectId,
                  record: (records.items || {})[it.id], onTracked: props.onTracked,
                  onDelete: function () { delItem(it.id); },
                });
              }))
            : h("p", { className: NS + "-sidehint" }, "还没有项，用下面的表单添加。"),
          zoteroItems.length
            ? h("div", { className: NS + "-findwrap" },
                zoteroItems.map(function (z) {
                  return h(FindRepoCard, {
                    key: z.id, objectId: objectId, zoteroItem: z, entry: entry,
                    onDone: props.onDone,
                  });
                }))
            : null,
          h(AddItemForm, { objectId: objectId, entry: entry, onDone: props.onDone }))
        : null);
    }

    // ---------------------------------------------------------------- 主面板
    function StudyEntriesPanel(props) {
      var objectId = props.objectId;
      var [entries, setEntries] = useState([]);
      var [records, setRecords] = useState(null);
      var [loading, setLoading] = useState(true);
      var [error, setError] = useState(null);
      var [newTitle, setNewTitle] = useState("");
      var [newNote, setNewNote] = useState("");
      var [busy, setBusy] = useState(false);
      var [note, setNote] = useState(null);
      var rootRef = SDK.hooks.useRef(null);
      var width = usePanelWidth(rootRef);
      var stacked = width > 0 && width < 860;

      var reload = useCallback(function () {
        API.studyEntries(objectId)
          .then(function (d) { setEntries(d.entries || []); setError(null); })
          .catch(function (e) { setError(msg(e)); });
        API.studyRecords(objectId)
          .then(function (d) { setRecords(d); })
          .catch(function () { setRecords(null); });
      }, [objectId]);

      useEffect(function () {
        setLoading(true); setNote(null);
        reload();
        setLoading(false);
      }, [reload]);

      function createEntry() {
        var t = newTitle.trim();
        if (!t) { setNote("子条目标题必填"); return; }
        setBusy(true); setNote(null);
        API.saveStudyEntry(objectId, { title: t, note: newNote.trim() || null })
          .then(function () { setNewTitle(""); setNewNote(""); reload(); })
          .catch(function (e) { setNote("新建失败：" + msg(e)); })
          .then(function () { setBusy(false); });
      }

      if (loading) return h("div", { className: NS + "-root" }, h(Spinner, { label: "读取子条目…" }));
      if (error) return h(Err, { title: "子条目读取失败", message: error });

      var total = (records && records.total) || 0;
      var last7 = (records && records.last_7d) || 0;

      return h("div", { className: NS + "-study" + (stacked ? " " + NS + "-study-stack" : ""), ref: rootRef },
        h("div", { className: NS + "-study-head" },
          h("div", null,
            h("strong", null, "自主学习子条目"),
            h("span", { className: NS + "-side-count" }, (entries.length || 0) + " 条")),
          h("div", { className: NS + "-study-stats" },
            h(Tag, { tone: "info" }, "总点击 " + total),
            h(Tag, { tone: "muted" }, "最近 7 天 " + last7),
            records && records.last_access
              ? h(Tag, { tone: "muted" }, "最近访问 " + String(records.last_access).slice(5, 16).replace("T", " "))
              : null)),
        h("div", { className: stacked ? NS + "-study-cols-stack" : NS + "-study-cols" },
          h("div", { className: NS + "-study-list" },
            h("div", { className: NS + "-pane-head" }, h("strong", null, "子条目")),
            entries.length
              ? h("ul", { className: NS + "-entrylist" }, entries.map(function (e) {
                  return h("li", {
                    key: e.id, className: NS + "-entryrow",
                  }, h("span", { className: NS + "-entry-name" }, e.title),
                    h("span", { className: NS + "-filemeta" }, (e.items || []).length + " 项"));
                }))
              : h("p", { className: NS + "-sidehint" }, "还没有子条目，在右侧新建。"),
            h("div", { className: NS + "-addentry" },
              h("input", {
                className: NS + "-input", value: newTitle,
                placeholder: "新子条目标题（必填）",
                onChange: function (e) { setNewTitle(e.target.value); },
              }),
              h("input", {
                className: NS + "-input", value: newNote,
                placeholder: "备注（可选）",
                onChange: function (e) { setNewNote(e.target.value); },
              }),
              h("button", {
                className: NS + "-btn " + NS + "-btn-primary", disabled: busy, onClick: createEntry,
              }, busy ? "新建中…" : "新建子条目")),
            note ? h("p", { className: NS + "-sidehint" }, note) : null),
          h("div", { className: NS + "-study-detail" },
            entries.length
              ? entries.map(function (e) {
                  return h(EntryDetail, {
                    key: e.id, entry: e, objectId: objectId, records: records, onDone: reload,
                    onTracked: reload,
                  });
                })
              : h(Empty, { title: "还没有子条目",
                          hint: "在左侧填标题新建一个，再给它添加论文 / 仓库 / 视频 / 网站。" }))))
    }

    return { StudyEntriesPanel: StudyEntriesPanel };
  }

  root.WBL_STUDY = { make: make };
})(typeof window !== "undefined" ? window : globalThis);
