/*
 * wb-learn 前端 · 外链面板（Zotero collections / GitHub repo，全部只读）
 *
 * 两类关联都只是「给 agent 与用户看的参考坐标」，本面板不发任何写请求到
 * Zotero 或 GitHub。登记动作（把 key / repo 存到 plugin-data）是本地写，
 * 有独立按钮，且失败原因原样显示（离线、gh 未登录、404 都能看见）。
 */
(function (root) {
  "use strict";

  var NS = "wbl";

  function make(SDK, API, portions) {
    var React = SDK.React;
    var h = React.createElement;
    var useState = SDK.hooks.useState;
    var useEffect = SDK.hooks.useEffect;
    var Empty = portions.Empty, Tag = portions.Tag, Spinner = portions.Spinner, Err = portions.Err;
    var msg = API.messageOf;

    function ZoteroCard(props) {
      var z = props.zotero || {};
      var cols = (props.collections && props.collections.collections) || [];
      var [picked, setPicked] = useState(z.configured_key || "");
      var [saving, setSaving] = useState(false);
      var [note, setNote] = useState(null);
      var [reading, setReading] = useState(null);      // 正在送 TextFold 的条目 key
      var [readNote, setReadNote] = useState(null);

      // 送阅读：只读 Zotero 元数据 → 交给 TextFold 生成 → 完成后打开阅读器
      function sendReading(it) {
        if (reading) return;
        setReading(it.key); setReadNote(null);
        SDK.fetchJSON("/api/plugins/wb-learn/zotero/read", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: it.key, lang: "zh" }),
        }).then(function (res) {
          if (!res || !res.ok) {
            setReading(null);
            setReadNote((res && res.error) || "发送失败");
            return;
          }
          var job = res.job || {};
          var pid = res.paper || job.pid;
          var openReader = function () {
            window.open("http://127.0.0.1:3017/p/" + encodeURIComponent(pid) + "?lang=zh",
              "_blank", "noopener");
          };
          // 立刻打开阅读器：边生成边读，不用等全部层级
          if (pid) { openReader(); setReading(null); }
          if (!job.id) { if (!pid) setReading(null); return; }
          var tick = function () {
            SDK.fetchJSON("/api/plugins/wb-learn/zotero/job?job=" + encodeURIComponent(job.id))
              .then(function (r) {
                if (!r || !r.ok) { setTimeout(tick, 2000); return; }
                if (r.job.status === "done") { if (!pid) openReader(); setReading(null); }
                else if (r.job.status === "error") { setReading(null); setReadNote("生成失败：" + (r.job.error || "")); }
                else setTimeout(tick, 2000);
              })
              .catch(function () { setTimeout(tick, 2000); });
          };
          tick();
        }).catch(function (e) { setReading(null); setReadNote("发送失败：" + msg(e)); });
      }

      useEffect(function () { setPicked(z.configured_key || ""); setNote(null); }, [z.configured_key]);

      function save(key) {
        setSaving(true); setNote(null);
        API.saveLinks(props.objectId, { zotero: key || null })
          .then(function () { setNote("已保存"); if (props.onReload) props.onReload(); })
          .catch(function (e) { setNote("保存失败：" + msg(e)); })
          .then(function () { setSaving(false); });
      }

      var available = props.collections && props.collections.available;
      var resolution = z.resolution || {};

      return h("section", { className: NS + "-linkcard" },
        h("header", { className: NS + "-linkhead" },
          h("strong", null, "Zotero collection"),
          h(Tag, { tone: "muted" }, "只读"),
          available ? h(Tag, { tone: "ok" }, cols.length + " 个集合")
                    : h(Tag, { tone: "warn" }, "不可用")),
        !available
          ? h("div", null,
              h("p", { className: NS + "-err" },
                (props.collections && props.collections.error) || "Zotero 本地 API 不可用"),
              h("p", { className: NS + "-sidehint" },
                "端点：" + ((props.collections && props.collections.base) || "127.0.0.1:23119") +
                "（Zotero 需在运行，且开启本地 API）"))
          : h("div", { className: NS + "-linkrow" },
              h("select", {
                className: NS + "-select", disabled: saving, value: picked,
                onChange: function (e) { setPicked(e.target.value); },
              },
                h("option", { value: "" }, "（不关联）"),
                cols.map(function (c) {
                  return h("option", { key: c.key, value: c.key }, c.name + "  [" + c.key + "]");
                })),
              h("button", {
                className: NS + "-btn " + NS + "-btn-quiet", disabled: saving,
                onClick: function () { save(picked); },
              }, "保存登记")),
        z.error ? h("p", { className: NS + "-err" }, z.error) : null,
        resolution.status === "auto"
          ? h("p", { className: NS + "-sidehint" }, "当前未登记设置；子条目论文 picker 自动使用同名集合：" + resolution.collection.path + " [" + resolution.collection.key + "]（只读，不会写回 Zotero）")
          : resolution.status === "ambiguous"
            ? h("p", { className: NS + "-sidehint" }, "找到多个与项目同名的集合；添加论文时会要求选择集合路径。也可在此显式登记一个 collection。")
            : resolution.status === "none"
              ? h("p", { className: NS + "-sidehint" }, resolution.error + "。也可在此显式登记 collection。") : null,
        note ? h("p", { className: NS + "-sidehint" }, note) : null,
        !props.configOnly && z.collection
          ? h("div", { className: NS + "-linkbody" },
              h("p", { className: NS + "-path" }, z.collection.name + " · " + z.collection.key),
              (z.items || []).length
                ? h("ul", { className: NS + "-reflist" }, z.items.slice(0, 12).map(function (it) {
                    return h("li", { key: it.key },
                      h("span", { className: NS + "-refname" }, it.title),
                      h("span", { className: NS + "-filemeta" },
                        (it.authors || []).join(", ") + (it.date ? " · " + it.date : "")),
                      h("button", {
                        className: NS + "-btn " + NS + "-btn-quiet",
                        disabled: !!reading,
                        title: "交给 TextFold 按 1–5 级折读（按 DOI/arXiv 取线上版本）",
                        onClick: function () { sendReading(it); },
                      }, reading === it.key ? "发送中…" : "送阅读"));
                  }))
                : h("p", { className: NS + "-sidehint" }, "这个集合里没有条目。"),
              readNote ? h("p", { className: NS + "-sidehint" }, readNote) : null)
          : (!props.configOnly ? h("p", { className: NS + "-sidehint" }, "还没有登记 collection。只读，不写 Zotero。") : null));
    }

    function GithubCard(props) {
      var g = props.github || {};
      var detail = g.detail || {};
      var [input, setInput] = useState(g.configured || g.repo || "");
      var [saving, setSaving] = useState(false);
      var [note, setNote] = useState(null);

      useEffect(function () { setInput(g.configured || g.repo || ""); }, [g.configured, g.repo]);

      function save() {
        var value = input.trim();
        setSaving(true); setNote(null);
        API.saveLinks(props.objectId, { github: value || null })
          .then(function () { setNote("已保存"); if (props.onReload) props.onReload(); })
          .catch(function (e) { setNote("保存失败：" + msg(e)); })
          .then(function () { setSaving(false); });
      }

      return h("section", { className: NS + "-linkcard" },
        h("header", { className: NS + "-linkhead" },
          h("strong", null, "GitHub 仓库"),
          h(Tag, { tone: "muted" }, "只读"),
          g.repo ? h(Tag, { tone: "ok" }, g.repo) : h(Tag, { tone: "muted" }, "未关联"),
          g.source ? h(Tag, { tone: "info" }, g.source === "manual" ? "手动登记" : "本地 .git") : null),
        h("div", { className: NS + "-linkrow" },
          h("input", {
            className: NS + "-input", value: input, disabled: saving,
            placeholder: "https://github.com/<owner>/<repo>",
            onChange: function (e) { setInput(e.target.value); },
          }),
          h("button", {
            className: NS + "-btn " + NS + "-btn-quiet", disabled: saving,
            onClick: save,
          }, "保存登记")),
        h("p", { className: NS + "-sidehint" },
          "只接受 github.com/<owner>/<repo>；其它主机一律拒绝（不做 URL 代理）。" +
          (g.local_remote ? " 本地 origin：" + g.local_remote : "")),
        note ? h("p", { className: NS + "-sidehint" }, note) : null,
        detail.ok
          ? h("div", { className: NS + "-linkbody" },
              h("p", null, h("a", { href: detail.url, target: "_blank", rel: "noreferrer" }, detail.repo),
                detail.description ? h("span", { className: NS + "-sidehint" }, " — " + detail.description) : null),
              h("p", { className: NS + "-sidehint" },
                "★ " + (detail.stars == null ? "?" : detail.stars) +
                " · " + (detail.language || "?") +
                " · license " + (detail.license || "未知") +
                " · " + (detail.via === "gh" ? "经 gh" : "匿名 HTTPS")),
              (detail.commits || []).length
                ? h("ul", { className: NS + "-reflist" }, detail.commits.map(function (c) {
                    return h("li", { key: c.sha },
                      h("code", null, c.sha),
                      h("span", null, " " + c.message),
                      h("span", { className: NS + "-filemeta" }, " " + (c.date || "")));
                  }))
                : (detail.commit_error ? h("p", { className: NS + "-err" }, "提交列表不可用：" + detail.commit_error) : null))
          : (g.repo ? h("p", { className: NS + "-err" },
              "仓库信息不可用：" + ((detail && detail.error) || "未知原因")) : null));
    }

    function LinksPanel(props) {
      var [data, setData] = useState(null);
      var [error, setError] = useState(null);
      var [token, setToken] = useState(0);

      useEffect(function () {
        var alive = true;
        setError(null);
        API.links(props.objectId)
          .then(function (d) { if (alive) setData(d); })
          .catch(function (e) { if (alive) setError(msg(e)); });
        return function () { alive = false; };
      }, [props.objectId, token]);

      if (error) return h(Err, { title: "外链读取失败", message: error });
      if (!data) return h(Spinner, { label: "读取 Zotero / GitHub…" });

      return h("div", { className: NS + "-links" },
        !props.configOnly ? h("p", { className: NS + "-sidehint" }, data.policy) : null,
        h(ZoteroCard, {
          objectId: props.objectId, zotero: data.zotero,
          collections: data.zotero_collections,
          onReload: function () { setToken(token + 1); },
        }),
        !props.configOnly ? h(GithubCard, {
          objectId: props.objectId, github: data.github,
          onReload: function () { setToken(token + 1); },
        }) : null);
    }

    return { LinksPanel: LinksPanel };
  }

  root.WBL_LINKS = { make: make };
})(typeof window !== "undefined" ? window : globalThis);
