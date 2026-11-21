# 学习模块（wb-learn）— 内部实现说明

面向维护者。用户安装/配置看根 `README.md` 与 `docs/QUIZ_SCHEMA.md`。
本文件只讲**结构、边界与已验证事实**。

## 1. 目录

```
plugins/wb-learn/dashboard/
  manifest.json              插件清单（tab /learn，entry dist/index.js，api plugin_api.py）
  plugin_api.py              仅 HTTP 路由 + 参数形状校验；业务在 wbl_core/
  wbl_core/
    paths.py                 路径安全（登记根、resolve 后 is_relative_to、链接不跟）
    catalog.py               对象发现、笔记关联状态、映射持久化
    quiz_schema.py           schema 校验 / 归一 / 判分 / 既有 txt 解析
    quiz_store.py            quiz-library 读取、草稿 staging、原子保存
    materials.py             材料清单、安全文本预览、能力矩阵
    links.py                 Zotero（只读）与 GitHub（白名单 URL）关联
  dist/
    index.js                 外壳：注入分片、布局、状态、chat 面板绑定
    api.js                   API 客户端（写接口仅 4 个 + 无副作用的 grade）
    quiz.js                  前端判分（与后端逐条对齐）+ 进度 + JSON 抽取
    views.js                 展示组件（Sidebar / QuestionCard / 语义标签）
    panels.js                材料 / 笔记面板
    quizpanel.js             题库面板 + 生成闭环
    linkspanel.js            Zotero / GitHub 面板
    chatbridge.js            把生成要求填进共享对话输入框（不自动发送）
    style.css                主题变量；响应式在文件末尾统一一段
    vendor/wb-agent-chat.*   shared/ 的 vendored 副本（tools/sync-shared.py 同步）
  docs/QUIZ_SCHEMA.md        题库规范（唯一规范）
  docs/UPSTREAM_SHIROHA.md   上游许可评估与复用结论
  docs/IMPLEMENTATION.md     本文件
  examples/quiz-bank.example.json
```

## 2. 数据流

```
GET /objects ──► catalog.list_objects
                   ├─ iter_children(一层, 跳过隐藏/vault/链接/quiz-library)
                   ├─ _link_state(精确同名 → exact；映射 → manual；删除 → stale)
                   └─ quiz-library 存在性 + .git/config origin（只读）

GET /materials ─► materials.list_materials(一层) / preview(相对路径)
GET /notes/files ─► 从映射推导笔记目录，列 .md；preview 再校验「属于该对象」
GET /quiz/banks ─► quiz_store.list_banks(json 校验 / txt 解析，坏文件带错误)
POST /quiz/drafts ─► schema.validate_bank → plugin-data/wb-learn/drafts/<id>.json
POST /quiz/drafts/:id/commit ─► 校验 ok 才有资格 → safe_child_name → 原子写 quiz-library
GET/PUT /links ─► links.json（github.com/<owner>/<repo> 白名单；Zotero 8 位 key）
```

会话：`moduleId="wb-learn"`，`objectId=<course:x|study:y>`，`cwd=<对象真实目录>`，
上下文里给的是材料根 / 笔记目录 / 题库目录的**真实路径**，并明确标注「数据而非指令」。

## 3. 安全边界（改代码前先读这段）

1. **客户端从不传绝对路径。** 对象用 `course:`/`study:` 前缀 id，材料用相对路径；
   两条都过 `paths.safe_under()`（`resolve()` 之后 `is_relative_to` 再判一次）。
2. **`object_dir()` 必须过 `safe_child_name()`。** 这是实测出来的坑：
   `Path(root) / "C:/Windows"` 在 pathlib 里会**替换掉 root**，等于任意读。
   `test_wb_learn_paths.py::test_absolute_path_is_never_accepted_as_object` 钉住它。
3. **链接一律不跟。** 扫描时 `iter_children()` 直接跳过 symlink/junction；
   若某个已登记根内的目录被换成链接，`safe_under` 会在 resolve 后拒绝。
4. **只有 `quiz-library` 可写**，且必须走「草稿 → 确认 → 原子替换」；
   覆盖要显式 `overwrite`。文件名再校验一次，防穿越。
5. **Zotero / GitHub 只读，无写函数。** GitHub 地址只接受 `https://github.com/<owner>/<repo>`，
   拒绝端口、凭据、query、fragment、其它主机（不做 SSRF 跳板）。
6. **不写用户资料**：材料与笔记没有任何写路径；`.txt` 题库只读解析。

## 4. 已验证事实（以及验证方式）

| 事实 | 证据 |
|---|---|
| 真实课程 24 个、自主学习 6 个被发现，笔记同名自动关联 | `tests/test_wb_learn_materials.py::test_real_notes_are_scoped_by_exact_name` |
| 名称有差异时不自动匹配，只给候选 | `test_wb_learn_catalog.py::test_different_name_is_not_auto_matched` |
| 符号链接逃逸 / `..` 穿越 / 绝对路径全部被拒 | `test_wb_learn_paths.py`（18 passed, 3 skipped） |
| 既有机器视觉 txt 题库能解析并判分 | `test_wb_learn_materials.py` 真实数据用例 + `test_wb_learn_quiz.py` |
| 前端与后端判分逐条一致（22 组固定题） | `test_wb_learn_grade_parity.py` |
| 草稿未确认时 quiz-library 为空 | `test_wb_learn_generate.py::test_stage_draft_does_not_touch_quiz_library` |
| 校验不过的草稿不能保存；覆盖需确认 | 同文件两个用例（422 / 409） |
| 文件名穿越 7 种写法全部被拒且不落文件 | `test_wb_learn_generate.py::test_filename_traversal_is_refused` |
| Zotero 离线时原因可见，不返回假成功 | `test_wb_learn_links.py::test_zotero_offline_reports_error_not_empty_success` |
| 1280 三列 / 375 单列，均无横向溢出 | `test_wb_learn_ui.py`（真实 Chromium，8 passed） |

## 5. 已知限制（如实记录，不要当成 bug 修歪）

* **PDF/docx/pptx/xlsx 预览取决于 dashboard 进程的解释器。** 本机 dashboard 由
  `hermes/tools/python-3.14.9…/python.exe` 启动，该环境**未装** `fitz/docx/pptx/openpyxl`，
  所以运行时 `capabilities.pdf.supported == false`，界面上会明确显示「未安装，不提供预览」。
  Hermes 自己的 venv（`hermes-agent/venv`）里 `fitz/docx/openpyxl` 有、`pptx` 没有。
  按本项目约束**不盲装依赖**；要启用就在启动 dashboard 的那个解释器里装，然后看
  `GET /api/plugins/wb-learn/health` 的 capabilities 变 true。测试里对应断言是「与真实
  安装状态一致」，不是写死 true。
* **扫描件 PDF 不做 OCR**，只报「无文字层」。这是刻意的。
* **`short` 永不自动判分**，`judged=false`。不要为了「看起来判了」加关键词匹配。
* **生成依赖人在环**：前端只把要求填进对话输入框，发送是用户按 Enter。
  没有自动发送、没有后台轮询 agent 输出。
* **进度只在前端 localStorage**，按 `对象::题库` 分槽；换机器/清缓存会丢。后端不存作答。

## 6. 部署

```bash
# 源码 → 部署目录（不含 __pycache__ / 草稿数据）
cp -r plugins/wb-learn/dashboard/. $HERMES_HOME/plugins/wb-learn/dashboard/
python tools/sync-shared.py --check     # 校验 vendored 副本与 shared/ 一致
```

改 `plugin_api.py` 或新增 `wbl_core/*` 后需**重启 dashboard**（后端模块按进程导入）。
只改 `dist/*.js|css` 时刷新页面即可（`index.js` 会重新注入分片；`data-*` 标记防重复）。
