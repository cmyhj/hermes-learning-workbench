/*
 * wb-learn — 学习区：课程 / 自主学习对象 + 题库刷题 + 模块级 agent 对话
 *
 * ── 层级（先定，再写 markup）──────────────────────────────────────────
 * 主（primary）：当前对象的题目卡（题干 / 选项 / 提交 / 判分 / 解析）
 * 次（secondary）：左栏对象列表（课程 + 自主学习，选中态唯一）
 * 三（tertiary）：对象标签页（题库/材料/笔记/关联）、题型过滤、进度计数
 *
 * 一个主动作：选对象 → 做题。生成题库、登记外链是对象的次动作，各有独立入口。
 *
 * ── 分期加载 ─────────────────────────────────────────────────────────
 * api.js / quiz.js / views.js / panels.js / quizpanel.js / linkspanel.js / chatbridge.js
 * 都在同目录，按序注入（插件资产路径 = /dashboard-plugins/wb-learn/dist/… 属同源，
 * 不违反 script-src 'self'）。拆开是为了每块能单独测、单独读。
 *
 * ── 复用（不改 shared/）──────────────────────────────────────────────
 * 对话面板来自 shared/wb-agent-chat/ 的 vendored 副本（dist/vendor/，由
 * tools/sync-shared.py 同步）。共享组件没有程序化发送接口，因此 chatbridge.js
 * 把生成要求填进输入框，发送仍是用户按 Enter 的动作。
 *
 * 上下文隔离：会话按「对象 id」分槽（course:机器视觉 / study:RL教学），cwd = 对象真实目录。
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var useMemo = SDK.hooks.useMemo;
  var fetchJSON = SDK.fetchJSON;

  var NS = "wbl";
  var DIST = "/dashboard-plugins/wb-learn/dist/";
  var SELECT_KEY = "wbl.object.v1";
  var TABS = [
    { id: "quiz", label: "题库 / 刷题" },
    { id: "materials", label: "材料" },
    { id: "notes", label: "笔记" },
    { id: "study", label: "子条目" },
    { id: "links", label: "Zotero 设置" },
  ];

  // ---------------------------------------------------------------- 资产装载
  function ensureCss() {
    var href = DIST + "style.css";
    if (document.querySelector('link[href="' + href + '"]')) return;
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  var loadPromise = null;
  function injectScript(src, marker) {
    return new Promise(function (resolve, reject) {
      if (document.querySelector("script[" + marker + "]")) { resolve(); return; }
      var s = document.createElement("script");
      s.src = src;
      s.async = false;
      s.setAttribute(marker, "1");
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("脚本加载失败：" + src)); };
      document.head.appendChild(s);
    });
  }

  function loadParts() {
    if (loadPromise) return loadPromise;
    var chain = Promise.resolve();
    [["api.js", "data-wbl-api"], ["quiz.js", "data-wbl-quiz"], ["views.js", "data-wbl-views"],
     ["panels.js", "data-wbl-panels"], ["quizpanel.js", "data-wbl-quizpanel"],
     ["linkspanel.js", "data-wbl-linkspanel"], ["studyentries.js", "data-wbl-study"],
     ["chatbridge.js", "data-wbl-chatbridge"]]
      .forEach(function (pair) {
        chain = chain.then(function () { return injectScript(DIST + pair[0], pair[1]); });
      });
    loadPromise = chain.then(function () {
      if (!window.WBL_API || !window.WBL_QUIZ || !window.WBL_VIEWS ||
          !window.WBL_QUIZPANEL || !window.WBL_LINKS || !window.WBL_PANELS || !window.WBL_CHATBRIDGE ||
          !window.WBL_STUDY) {
        throw new Error("部分的 dist 脚本没有挂上全局对象");
      }
      var api = window.WBL_API.make(fetchJSON);
      var views = window.WBL_VIEWS.make(SDK);
      return {
        API: api,
        QUIZ: window.WBL_QUIZ,
        VIEWS: views,
        PANELS: window.WBL_PANELS.make(SDK, api, views),
        QUIZPANEL: window.WBL_QUIZPANEL.make(SDK, api, views, window.WBL_QUIZ),
        LINKS: window.WBL_LINKS.make(SDK, api, views),
        STUDY: window.WBL_STUDY.make(SDK, api, views),
        BRIDGE: window.WBL_CHATBRIDGE,
      };
    });
    return loadPromise;
  }

  // ---------------------------------------------------------------- 共享聊天组件
  var CHAT_VENDOR = DIST + "vendor/";
  var chatPromise = null;
  function loadSharedChat() {
    if (window.__WB_CHAT__) return Promise.resolve(window.__WB_CHAT__);
    if (chatPromise) return chatPromise;
    chatPromise = new Promise(function (resolve, reject) {
      if (!document.querySelector("link[data-wb-chat-css]")) {
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = CHAT_VENDOR + "wb-agent-chat.css";
        link.setAttribute("data-wb-chat-css", "1");
        document.head.appendChild(link);
      }
      if (document.querySelector("script[data-wb-chat-js]")) {
        var prev = window.__WB_CHAT_READY__;
        window.__WB_CHAT_READY__ = function (api) {
          if (typeof prev === "function") prev(api);
          resolve(api);
        };
        return;
      }
      window.__WB_CHAT_READY__ = function (api) { resolve(api); };
      var s = document.createElement("script");
      s.src = CHAT_VENDOR + "wb-agent-chat.js";
      s.async = true;
      s.setAttribute("data-wb-chat-js", "1");
      s.onerror = function () {
        reject(new Error("共享聊天组件加载失败：" + s.src + "（运行 tools/sync-shared.py 后刷新）"));
      };
      document.head.appendChild(s);
    });
    return chatPromise;
  }

  // ---------------------------------------------------------------- 响应式
  // 断点必须按**插件容器的宽度**判断，不能按 window.innerWidth：
  // dashboard 自身还有一层侧边栏，1280 的窗口里主区只有 ~985px，
  // 用窗口宽度会误判成宽屏并把三列挤出去（实测过：对话列掉到第二行）。
  //
  // ``ready`` 是重测开关：首帧走的是「加载中」的早返回分支，那时 ref 还没挂上，
  // 不依赖 ready 的话 effect 在 ref.current === null 时直接 return，之后再也不会量。
  function useContainerWidth(ref, ready, fallback) {
    var [width, setWidth] = useState(fallback || 0);
    useEffect(function () {
      if (!ref.current) return;
      var el = ref.current;
      function measure() { setWidth(el.getBoundingClientRect().width); }
      measure();
      if (typeof ResizeObserver === "undefined") {
        window.addEventListener("resize", measure);
        return function () { window.removeEventListener("resize", measure); };
      }
      var ro = new ResizeObserver(measure);
      ro.observe(el);
      return function () { ro.disconnect(); };
    }, [ready]);
    return width;
  }

  window.__HERMES_PLUGINS__.register("wb-learn", function LearnPage() {
    // 注册时就确保样式在（manifest 的 css 字段由后端按进程缓存，新装插件要重启才生效）
    ensureCss();
    return h(LearnView, { SDK: SDK, h: h, NS: NS, fetchJSON: fetchJSON,
                          TABS: TABS, SELECT_KEY: SELECT_KEY, DIST: DIST,
                          loadParts: loadParts, loadSharedChat: loadSharedChat,
                          useState: useState, useEffect: useEffect,
                          useCallback: useCallback, useMemo: useMemo,
                          useRef: SDK.hooks.useRef,
                          useContainerWidth: useContainerWidth, React: React });
  });

  function LearnView(props) {
    var useState = props.useState, useEffect = props.useEffect;
    var useCallback = props.useCallback, useMemo = props.useMemo;
    var h = props.h, NS = props.NS, TABS = props.TABS, SELECT_KEY = props.SELECT_KEY;

    var [parts, setParts] = useState(null);
    var [partsError, setPartsError] = useState(null);
    var [data, setData] = useState(null);
    var [loadError, setLoadError] = useState(null);
    var [selected, setSelected] = useState(null);
    var [tab, setTab] = useState("quiz");
    var [reload, setReload] = useState(0);
    var [noteFiles, setNoteFiles] = useState([]);
    var [chatComp, setChatComp] = useState(null);
    var [chatError, setChatError] = useState(null);
    var [chatOpen, setChatOpen] = useState(true);
    var [chatDrawer, setChatDrawer] = useState(false);
    var [chatBusy, setChatBusy] = useState(false);
    var [sidebarKind, setSidebarKind] = useState("course");
    var [newKind, setNewKind] = useState(null);
    var [newName, setNewName] = useState("");
    var [newPreview, setNewPreview] = useState(null);
    var [newError, setNewError] = useState(null);
    var [newBusy, setNewBusy] = useState(false);
    var [promptNotice, setPromptNotice] = useState(null);
    var rootRef = props.useRef(null);
    // data 到位前走的是「加载中」早返回分支（没有 .wbl-root 挂 ref），
    // 所以把 !!data 作为重测开关传进去，否则永远量不到容器宽度。
    var containerWidth = props.useContainerWidth(rootRef, !!data, 1440);
    // 三列布局的实际下限（实测 1280 窗口 → 容器 985px 仍可三列）：
    // 264（对象）+ 280（对话）+ 间距 40 = 584，主区还剩约 400px，够放题干与选项。
    var narrow = containerWidth > 0 && containerWidth < 960;

    useEffect(function () {
      props.loadParts().then(setParts).catch(function (e) { setPartsError(String(e && e.message || e)); });
    }, []);

    useEffect(function () {
      props.loadSharedChat()
        .then(function (api) { setChatComp(function () { return api.makePanel(props.SDK); }); })
        .catch(function (e) { setChatError(String(e && e.message || e)); });
    }, []);

    useEffect(function () {
      if (!parts) return;
      setLoadError(null);
      parts.API.objects()
        .then(function (d) {
          if (!d || !Array.isArray(d.objects)) {
            setLoadError("后端返回形状不对（缺少 objects）；改过 plugin_api.py 需重启 dashboard。");
            return;
          }
          setData(d);
          if (!d.objects.length) return;
          var remembered = null;
          try { remembered = window.localStorage.getItem(SELECT_KEY); } catch (e) { remembered = null; }
          var hit = remembered && d.objects.some(function (o) { return o.id === remembered; });
          var initial = hit ? d.objects.find(function (o) { return o.id === remembered; }) : d.objects[0];
          if (initial) setSidebarKind(initial.kind);
          setSelected(function (prev) { return prev || (hit ? remembered : d.objects[0].id); });
        })
        .catch(function (e) { setLoadError(parts.API.messageOf(e)); });
    }, [parts, reload]);

    var current = useMemo(function () {
      if (!data || !data.objects) return null;
      for (var i = 0; i < data.objects.length; i++) {
        if (data.objects[i].id === selected) return data.objects[i];
      }
      return null;
    }, [data, selected]);

    useEffect(function () {
      if (!parts || !selected) return;
      setNoteFiles([]);
      parts.API.noteFiles(selected)
        .then(function (d) { setNoteFiles((d && d.files) || []); })
        .catch(function () { setNoteFiles([]); });
    }, [parts, selected, reload]);

    function selectObject(id) {
      setSelected(id);
      var picked = data && data.objects.find(function (o) { return o.id === id; });
      if (picked) setSidebarKind(picked.kind);
      setTab(String(id).indexOf("study:") === 0 ? "study" : "quiz");
      setPromptNotice(null);
      try { window.localStorage.setItem(SELECT_KEY, id); } catch (e) { /* 记不住不影响使用 */ }
    }

    var onReload = useCallback(function () { setReload(function (n) { return n + 1; }); }, []);

    function previewNewObject() {
      setNewError(null); setNewPreview(null);
      parts.API.previewObject(newKind, newName)
        .then(setNewPreview).catch(function (e) { setNewError(parts.API.messageOf(e)); });
    }
    function createNewObject() {
      if (!newPreview || newPreview.exists || newPreview.name !== newName.trim()) return;
      setNewBusy(true); setNewError(null);
      parts.API.createObject(newKind, newName)
        .then(function (r) {
          var object = r.object;
          setData(function (d) {
            var objects = (d.objects || []).filter(function (o) { return o.id !== object.id; }).concat([object]);
            return Object.assign({}, d, { objects: objects });
          });
          setSidebarKind(newKind); setNewKind(null); setNewName(""); setNewPreview(null);
          selectObject(object.id);
          setReload(function (n) { return n + 1; });
        }).catch(function (e) { setNewError(parts.API.messageOf(e)); })
        .then(function () { setNewBusy(false); });
    }

    function bindPrompt(text) {
      if (!parts || !parts.BRIDGE) return false;
      if (!chatComp || !parts.BRIDGE.chatReady()) {
        setPromptNotice("对话面板还没就绪：先在右侧对话里发一条消息建立会话，再点生成。");
        return false;
      }
      var ok = parts.BRIDGE.insertPrompt(text);
      setPromptNotice(ok ? "已把生成要求填入右侧对话输入框：按 Enter 发送给 Hermes agent。"
                         : "没找到对话输入框（面板可能已收起），展开对话后重试。");
      return ok;
    }

    function pullReply() {
      if (!parts || !parts.BRIDGE) return null;
      return parts.BRIDGE.lastAgentReply();
    }

    function chatPanel(extraClass, inDrawer) {
      if (!chatComp) return null;
      var obj = current;
      return h(chatComp, {
        key: inDrawer ? "chat-drawer" : "chat",
        moduleId: "wb-learn",
        objectId: obj ? obj.id : null,   // 槽位 = 对象 id：每个对象独立会话
        objectName: obj ? (obj.kind === "course" ? "课程 · " : "自学 · ") + obj.name : "模块级",
        className: extraClass,
        getContext: function () {
          if (!obj) {
            return {
              module: "学习区（wb-learn）",
              object: null,
              cwd: data && data.roots && data.roots.courses ? data.roots.courses.path : undefined,
              notes: ["未选择学习对象：这里只做模块级提问。"],
            };
          }
          var notes = obj.notes || {};
          var files = [{ path: obj.path, role: "学习材料根目录（只读）" }];
          if (notes.vault_path) files.push({ path: notes.vault_path, role: "Obsidian 笔记目录（只读）" });
          if (obj.quiz_library && obj.quiz_library.exists) {
            files.push({ path: obj.quiz_library.path, role: "题库目录（唯一允许写入）" });
          }
          var facts = [
            (obj.kind === "course" ? "课程" : "自主学习主题") + "：" + obj.name,
            "材料：" + obj.counts.files + " 个文件 / " + obj.counts.dirs + " 个子目录（一层计数）",
            notes.linked ? "笔记：已关联 " + notes.vault : "笔记：" + (notes.reason || "未关联"),
            "题库：" + (obj.quiz_library.exists ? "已有 quiz-library" : "还没有 quiz-library（可按需生成）"),
          ];
          if (notes.stale) facts.push("登记笔记目录当前不存在（stale）：不要猜别的目录");
          return {
            module: "学习区（wb-learn）：课程 / 自主学习",
            object: obj.name,
            cwd: obj.path,
            facts: facts,
            files: files,
            notes: [
              "材料与笔记按只读资料对待；引用必须给材料相对路径",
              "题库只能写进 " + (obj.quiz_library ? obj.quiz_library.path : obj.path + "/quiz-library") +
                "；生成先出 JSON 草稿，等人工确认，不要直接写文件",
              "不要修改 Obsidian 笔记、Zotero 数据，也不要 push 任何 GitHub 仓库",
            ],
          };
        },
        onBusy: setChatBusy,
        onCollapse: inDrawer ? function () { setChatDrawer(false); } : undefined,
      });
    }

    if (partsError) {
      return h("div", { className: NS + "-root" },
        h("div", { className: NS + "-err" }, "前端脚本加载失败：" + partsError));
    }
    if (!parts) return h("div", { className: NS + "-root" }, h("p", { className: NS + "-hint" }, "加载学习区…"));
    if (loadError) {
      return h("div", { className: NS + "-root" },
        h(parts.VIEWS.Err, { title: "读取学习对象失败", message: loadError }));
    }
    if (!data) return h("div", { className: NS + "-root" }, h("p", { className: NS + "-hint" }, "扫描课程与自学主题…"));

    var V = parts.VIEWS;
    var visibleTabs = current && current.kind === "course"
      ? TABS.filter(function (t) { return t.id === "quiz" || t.id === "materials" || t.id === "notes"; })
      : TABS.filter(function (t) { return t.id === "study" || t.id === "links"; });
    var activeTab = visibleTabs.some(function (t) { return tab === t.id; }) ? tab : "study";
    var tabs = h("nav", { className: NS + "-tabs" }, visibleTabs.map(function (t) {
      return h("button", {
        key: t.id,
        className: NS + "-tab" + (activeTab === t.id ? " " + NS + "-tab-active" : ""),
        onClick: function () { setTab(t.id); },
      }, t.label);
    }));

    var body;
    if (!current) {
      body = h(V.Empty, {
        title: "没有找到学习对象",
        hint: "扫描根：课程 " + data.roots.courses.path + " · 自主学习 " + data.roots.study.path +
              (data.errors && data.errors.length ? " · " + data.errors.join("；") : ""),
      });
    } else if (activeTab === "quiz") {
      body = h(parts.QUIZPANEL.QuizPanel, {
        objectId: current.id, object: current, reloadToken: reload,
        onBindPrompt: bindPrompt, onPullReply: pullReply, onSaved: onReload,
      });
    } else if (activeTab === "materials") {
      body = h(parts.PANELS.MaterialsPanel, { objectId: current.id });
    } else if (activeTab === "notes") {
      body = h(parts.PANELS.NotesPanel, {
        objectId: current.id, object: current, objects: data.objects,
        vaultCourses: data.vault_courses, noteFiles: noteFiles, onReindex: onReload,
      });
    } else if (activeTab === "study") {
      // 子条目只对「自主学习」对象有意义：课程对象如实说明，不伪造空面板
      body = current.kind === "study"
        ? h(parts.STUDY.StudyEntriesPanel, { objectId: current.id, object: current, onReload: onReload })
        : h(V.Empty, {
            title: "子条目只用于自主学习项目",
            hint: "当前是课程对象。课程的知识组织走「题库 / 刷题」「材料」「笔记」三个标签。",
          });
    } else {
      body = h(parts.LINKS.LinksPanel, { objectId: current.id, configOnly: current.kind === "study" });
    }

    var chatColumn = (!narrow && chatOpen && chatComp) ? chatPanel(NS + "-chatcol", false) : null;
    var drawer = (narrow && chatDrawer && chatComp)
      ? h("div", { key: "drawer", className: NS + "-drawer-layer" },
          h("div", { className: "wbc-drawer-mask", onClick: function () { setChatDrawer(false); } }),
          h("div", { className: "wbc-drawer" }, chatPanel("wbc-panel-drawer", true)))
      : null;

    return h("div", { className: NS + "-root" + (chatColumn ? " " + NS + "-root-chat" : ""), ref: rootRef },
      h(V.Sidebar, {
        objects: data.objects, selected: selected, roots: data.roots,
        kind: sidebarKind, onKindChange: function (kind) {
          setSidebarKind(kind);
          var first = data.objects.find(function (o) { return o.kind === kind; });
          if (first) selectObject(first.id);
        },
        onSelect: selectObject,
        onNew: function (kind) { setNewKind(kind); setNewName(""); setNewPreview(null); setNewError(null); },
      }),
      h("section", { className: NS + "-main" },
        h("header", { className: NS + "-head" },
          h("div", null,
            h("h1", { className: NS + "-title" }, current ? current.name : "学习"),
            h("p", { className: NS + "-subtitle" },
              current ? (current.kind === "course" ? "课程 · " : "自主学习 · ") + current.path
                      : "选择左侧对象")),
          h("select", { className: NS + "-mobile-object-picker", value: selected || "",
            "aria-label": "选择学习对象", onChange: function (e) { selectObject(e.target.value); } },
            data.objects.map(function (o) { return h("option", { key: o.id, value: o.id },
              (o.kind === "course" ? "课程 · " : "自主学习 · ") + o.name); })),
          h("div", { className: NS + "-head-actions" },
            !narrow ? h("button", {
              className: NS + "-btn " + NS + "-btn-quiet",
              onClick: function () { setChatOpen(!chatOpen); },
            }, chatOpen ? "收起对话" : "对话" + (chatBusy ? " ●" : ""))
            : h("button", {
              className: NS + "-btn " + NS + "-btn-quiet",
              onClick: function () { setChatDrawer(true); },
            }, "对话" + (chatBusy ? " ●" : "")))),
        newKind ? h("div", { className: NS + "-newobject", role: "dialog", "aria-modal": "true" },
          h("div", { className: NS + "-newobject-card" },
            h("h2", null, newKind === "course" ? "新建课程" : "新建自主学习"),
            h("label", null, "名称"),
            h("input", { className: NS + "-input", value: newName, maxLength: 120,
              onChange: function (e) { setNewName(e.target.value); setNewPreview(null); },
              placeholder: "输入文件夹名称" }),
            newPreview ? h("div", { className: NS + "-preview" },
              h("strong", null, "创建位置预览"), h("code", null, newPreview.path),
              newPreview.exists ? h("p", { className: NS + "-err" }, "目标已存在，不能覆盖。") : null) : null,
            newError ? h("p", { className: NS + "-err" }, newError) : null,
            h("div", { className: NS + "-linkrow" },
              h("button", { className: NS + "-btn " + NS + "-btn-quiet", onClick: previewNewObject }, "预览位置"),
              h("button", { className: NS + "-btn " + NS + "-btn-primary",
                disabled: newBusy || !newPreview || newPreview.exists || newPreview.name !== newName.trim(),
                onClick: createNewObject }, newBusy ? "创建中…" : "确认创建"),
              h("button", { className: NS + "-btn " + NS + "-btn-quiet", onClick: function () { setNewKind(null); } }, "取消")))) : null,
        tabs,
        chatError ? h("p", { className: NS + "-err" }, "对话组件不可用：" + chatError) : null,
        promptNotice ? h("p", { className: NS + "-notice" }, promptNotice) : null,
        body),
      chatColumn,
      drawer);
  }
})();
