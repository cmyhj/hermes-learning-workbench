/*
 * wb-learn 前端 · 资料 / 笔记 / 关联面板（全部只读，唯二写操作有显式按钮）
 *
 * 三个子面板共用一个「当前对象」：
 *   materials  材料树 + 文本预览（明确写出不支持的格式，不假装能读）
 *   notes      Obsidian 笔记列表 + 预览 + **显式选择映射**（唯一写入口之一）
 *   links      Zotero collection / GitHub repo（只读；登记是用户显式动作）
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

    // ---------------------------------------------------------------- 预览窗格
    function PreviewPane(props) {
      var p = props.preview;
      if (props.error) return h(Err, { title: "预览失败", message: props.error });
      if (!p) return props.loading ? h(Spinner, { label: "读取…" }) : h(Empty, { title: "选一个文件看内容" });
      return h("div", { className: NS + "-preview" },
        h("div", { className: NS + "-preview-head" },
          h("strong", { title: p.rel_path }, p.name),
          h(Tag, { tone: p.supported ? "info" : "warn" }, p.mode || p.kind),
          p.bytes != null ? h(Tag, { tone: "muted" }, (p.bytes / 1024).toFixed(1) + " KB") : null,
          p.encoding ? h(Tag, { tone: "muted" }, p.encoding) : null),
        p.notice ? h("p", { className: NS + "-err" }, p.notice) : null,
        p.text
          ? h("pre", { className: NS + "-text" }, p.text)
          : h("p", { className: NS + "-sidehint" }, "该文件没有可显示的文本内容。"));
    }

    // ---------------------------------------------------------------- 材料
    function MaterialsPanel(props) {
      var [listing, setListing] = useState(null);
      var [error, setError] = useState(null);
      var [preview, setPreview] = useState(null);
      var [previewErr, setPreviewErr] = useState(null);
      var [loadingPreview, setLoadingPreview] = useState(false);
      var [subdir, setSubdir] = useState("");

      useEffect(function () {
        var alive = true;
        setListing(null); setError(null); setPreview(null); setPreviewErr(null);
        API.materials(props.objectId, subdir)
          .then(function (d) { if (alive) setListing(d); })
          .catch(function (e) { if (alive) setError(msg(e)); });
        return function () { alive = false; };
      }, [props.objectId, subdir]);

      function open(item) {
        if (item.is_dir) { setSubdir(item.rel_path); return; }
        setLoadingPreview(true); setPreviewErr(null); setPreview(null);
        API.previewMaterial(props.objectId, item.rel_path)
          .then(setPreview)
          .catch(function (e) { setPreviewErr(msg(e)); })
          .then(function () { setLoadingPreview(false); });
      }

      if (error) return h(Err, { title: "资料清单读取失败", message: error });
      if (!listing) return h(Spinner, { label: "扫描材料…" });
      var caps = listing.capabilities || {};

      return h("div", { className: NS + "-pane2" },
        h("div", { className: NS + "-pane-list" },
          h("div", { className: NS + "-pane-head" },
            h("strong", null, subdir ? subdir : "材料（一层）"),
            subdir ? h("button", {
              className: NS + "-link", onClick: function () { setSubdir(""); },
            }, "← 根") : null,
            h("span", { className: NS + "-side-count" }, listing.count)),
          h("ul", { className: NS + "-filelist" }, (listing.items || []).map(function (item) {
            var selectable = !item.is_dir;
            return h("li", {
              key: item.rel_path,
              className: NS + "-file" + (item.is_dir ? " " + NS + "-file-dir" : ""),
              onClick: function () { open(item); },
              title: item.path,
            },
              h("span", { className: NS + "-filename" }, item.name + (item.is_dir ? "/" : "")),
              selectable ? h("span", { className: NS + "-filemeta" },
                (item.kind || "") + (item.bytes != null ? " · " + (item.bytes / 1024).toFixed(0) + "KB" : ""))
                : null);
          })),
          h("p", { className: NS + "-sidehint" },
            "PDF 预览：" + (caps.pdf && caps.pdf.supported ? "可用（文字层）" : "不可用（未装 PyMuPDF）") +
            " · 扫描件无文字层时不提取、不做 OCR")),
        h("div", { className: NS + "-pane-body" },
          h(PreviewPane, { preview: preview, error: previewErr, loading: loadingPreview })));
    }

    // ---------------------------------------------------------------- 笔记
    function NotesPanel(props) {
      var [objects, setObjects] = useState(null);
      var [preview, setPreview] = useState(null);
      var [previewErr, setPreviewErr] = useState(null);
      var [saving, setSaving] = useState(false);
      var [saveMsg, setSaveMsg] = useState(null);
      var pick = props.object || null;

      useEffect(function () { setObjects(props.objects); }, [props.objects]);
      useEffect(function () { setPreview(null); setPreviewErr(null); setSaveMsg(null); }, [props.objectId]);

      if (!pick) return h(Spinner, { label: "加载对象…" });
      var notes = pick.notes || {};
      var candidates = notes.candidates || (props.vaultCourses || []);

      function choose(vault) {
        setSaving(true); setSaveMsg(null);
        API.saveNoteLink(props.objectId, vault)
          .then(function () {
            setSaveMsg("已保存映射：" + (vault || "无笔记"));
            if (props.onReindex) props.onReindex();
          })
          .catch(function (e) { setSaveMsg("保存失败：" + msg(e)); })
          .then(function () { setSaving(false); });
      }

      // 笔记目录里的 md 列表：走 materials 的 preview 需要一个相对路径，
      // 但笔记不在对象目录下 —— 直接用对象自身的 vault 路径列目录（后端 /materials 只认对象目录）。
      // 因此这里用 push 的方式：由 index.js 传 noteFiles（已通过 /materials?… 之外的方式取到的列表）。
      var files = props.noteFiles || [];

      return h("div", { className: NS + "-notes" },
        h("div", { className: NS + "-notecard" },
          h("div", { className: NS + "-notecard-head" },
            h("strong", null, "Obsidian 笔记关联"),
            h(Tag, { tone: notes.linked ? (notes.kind === "exact" ? "ok" : "info") : (notes.stale ? "warn" : "muted") },
              notes.kind === "exact" ? "精确同名" : notes.kind === "manual" ? "用户指定" : notes.kind === "none" ? "已排除" : "未关联")),
          h("p", { className: NS + "-sidehint" }, notes.reason || ""),
          notes.vault_path ? h("p", { className: NS + "-path" }, notes.vault_path) : null,
          h("div", { className: NS + "-notepick" },
            h("label", null, "选择笔记目录"),
            h("select", {
              className: NS + "-select", disabled: saving,
              value: notes.vault || "",
              onChange: function (e) { choose(e.target.value || null); },
            },
              h("option", { value: "" }, notes.kind === "exact" ? "（保持精确同名关联）" : "（不关联笔记）"),
              candidates.map(function (name) {
                return h("option", { key: name, value: name }, name);
              })),
            saving ? h("span", { className: NS + "-sidehint" }, "保存中…") : null),
          saveMsg ? h("p", { className: NS + "-sidehint" }, saveMsg) : null,
          h("p", { className: NS + "-sidehint" },
            "不做模糊自动匹配：目录名不同就必须在这里显式选择，选择会持久化到 plugin-data。")),
        notes.linked
          ? h("div", { className: NS + "-pane-body" },
            previewErr ? h(Err, { title: "笔记预览失败", message: previewErr }) : null,
            h(PreviewPane, { preview: preview }),
            files.length
              ? h("ul", { className: NS + "-filelist" }, files.map(function (f) {
                  return h("li", {
                    key: f.rel_path, className: NS + "-file",
                    onClick: function () {
                      setPreviewErr(null);
                      API.previewNote(props.objectId, f.rel_path)
                        .then(setPreview).catch(function (e) { setPreviewErr(msg(e)); });
                    },
                  }, h("span", { className: NS + "-filename" }, f.name));
                }))
              : h("p", { className: NS + "-sidehint" }, "这个笔记目录里没有 .md 文件。"))
          : h(Empty, { title: "还没有关联笔记",
                       hint: notes.stale ? "登记的目录已不存在，重新选一个。" : "在上面的下拉框里显式选择。" }));
    }

    return { MaterialsPanel: MaterialsPanel, NotesPanel: NotesPanel, PreviewPane: PreviewPane };
  }

  root.WBL_PANELS = { make: make };
})(typeof window !== "undefined" ? window : globalThis);
