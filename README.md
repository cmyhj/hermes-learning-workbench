# hermes-learning-workbench

Hermes Agent 的**学习区插件**（`wb-learn`）：课程 / 自主学习对象的材料与笔记、题库刷题与生成、
只读的 Zotero 与 GitHub 关联，以及每个学习对象独立的 agent 会话。

它不是一个独立的刷题 App，而是**挂在 Hermes Web 控制台里的一个页签**（`/learn`）。所有 AI 能力
仍然走 Hermes 自己的 agent：插件不引入第二套推理链路、不自己调另一个 LLM。

```
课程/自学对象 ──┬─ 材料（只读，一层）
                ├─ Obsidian 笔记（精确同名自动关联；名称不同必须你显式选）
                ├─ 题库 quiz-library/（标准 JSON 或既有 txt，做题/判分/解析/进度）
                ├─ 只读关联（Zotero collection 元数据 / GitHub 仓库元数据）
                └─ 独立 agent 会话（cwd = 该对象真实目录）
```

---

## 1. 它做什么 / 不做什么

**做**
- 扫描两个已登记根：课程材料目录、自主学习目录，各列**一层**（不递归全盘）。
- 把课程/主题与 Obsidian 笔记目录关联：**目录名精确同名**才自动关联；名称有差异时只列候选，
  等你在 UI 里显式选择，并把选择存到本地（`plugin-data/wb-learn/learn-mapping.json`）。
- 做题：单选 / 多选 / 判断 / 填空（含多空）/ 简答；判分与进度在浏览器本地。
- 题库生成闭环：无题库时显示生成入口 → 把「基于真实材料与笔记生成标准题库」的要求交给
  **你正在用的 Hermes agent** → 你把 agent 产出的 JSON 贴回来 → 校验 → **你确认** → 原子写入
  `quiz-library/`。
- 只读外链：Zotero collection 条目元数据、GitHub 仓库元数据与最近提交。

**不做（刻意的边界）**
- 不写你的课程材料、不写 Obsidian 笔记、不写 Zotero、不 push 任何 GitHub 仓库。
- 唯一的写目标：`<对象>/quiz-library/`（题库）与插件自己的 `plugin-data/wb-learn/`（映射/链接登记/草稿）。
- 不做 OCR（扫描件 PDF 抽不到文字会明确提示）；不自动判分主观题（不做关键词假判）。
- 不迁移、不改写你已有的题库文件；既有 `.txt` 题库只读解析。
- 不在后台偷偷调用 agent：生成要求填进对话输入框，**发送是你按 Enter**。

---

## 2. 安装

### 2.1 前置
- 已安装 Hermes Agent，并能启动 Web 控制台：`hermes dashboard`（**不是** `hermes serve` ——
  serve 是 headless 的，没有浏览器 UI，但插件后端路由在两者下都会挂载）。
- 插件**必须**列进 `config.yaml` 的 `plugins.enabled`，否则后端 Python 不会被导入。

### 2.2 复制到 profile 的 plugins 目录

```bash
# Windows（Git Bash / MSYS）
HERMES_HOME="${HERMES_HOME:-$LOCALAPPDATA/hermes}"
cp -r wb-learn "$HERMES_HOME/plugins/"
cp shared/wb-agent-chat/* "$HERMES_HOME/plugins/wb-learn/dashboard/dist/vendor/"
```

```bash
# Linux / macOS
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
cp -r wb-learn "$HERMES_HOME/plugins/"
cp shared/wb-agent-chat/* "$HERMES_HOME/plugins/wb-learn/dashboard/dist/vendor/"
```

> `dist/vendor/` 里已经带了 `wb-agent-chat.{js,css}` 的副本；上面的 `cp` 是为了确认它与
> `shared/` 一致（来源说明见 `NOTICE`）。

### 2.3 启用插件（唯一正确方式）

`hermes plugins enable <name>` **对 dashboard 插件无效**（它只认 agent 插件，会报
`No plugin named 'wb-learn'`）。唯一杠杆是 `plugins.enabled`：

```bash
# 先读当前列表，别丢条目（setter 会整体替换数组）
hermes config get plugins.enabled

# 然后把 wb-learn 加进去（保持已有条目）
hermes config set plugins.enabled '["disk-cleanup","web-search-plus","wb-project","wb-learn"]'
```

或者用自带脚本（会扫描磁盘上的 manifest 并保留非 wb 条目）：

```bash
python tools/wb-enable.py            # 干跑：打印将写入的列表
python tools/wb-enable.py --apply    # 真的写 config.yaml
```

### 2.4 重启并验证

```bash
# 改过 plugin_api.py / 新增 wbl_core/* → 必须重启（后端模块按进程导入）
hermes gateway restart     # 或按你的部署方式重启 dashboard

# 验证后端真的挂上了（把端口换成你自己的）
curl -s -H "X-Hermes-Session-Token: $TOK" http://127.0.0.1:9119/api/plugins/wb-learn/health
```

期望返回里有 `"ok": true`、`"version": "2.0.0"`、`objects` 计数与 `capabilities`。
`401 Unauthorized` 说明缺会话 token（见 §6.2），不是插件坏了。

---

## 3. 目录布局

```
hermes-learning-workbench/
├── wb-learn/dashboard/          ← 插件本体（复制到 $HERMES_HOME/plugins/wb-learn/）
│   ├── manifest.json            页签 /learn、entry、api
│   ├── plugin_api.py            仅 HTTP 路由；业务在 wbl_core/
│   ├── wbl_core/                paths catalog quiz_schema quiz_store materials links
│   ├── dist/                    index api quiz views panels quizpanel linkspanel chatbridge style
│   │   └── vendor/              wb-agent-chat.{js,css}（vendored 副本）
│   ├── docs/                    QUIZ_SCHEMA.md / UPSTREAM_SHIROHA.md / IMPLEMENTATION.md
│   └── examples/                quiz-bank.example.json
├── shared/wb-agent-chat/        ← 共享对话组件的唯一来源（vendored 到上面的 dist/vendor）
├── tests/                       pytest + node 测试（scratch 伪课程，不碰真实资料）
├── tools/
│   ├── sync-shared.py           把 shared/ 同步到各插件 dist/vendor（含 --check）
│   ├── wb-enable.py             生成 plugins.enabled 列表
│   └── export-public.py         本仓库的导出脚本（导出时自检隐私）
├── config.example.yaml          插件相关配置示例
├── LICENSE / NOTICE
└── README.md
```

运行时数据（**不在仓库里**）：

```
$HERMES_HOME/plugin-data/wb-learn/
├── learn-mapping.json           对象 → 笔记目录（你显式选过的）
├── links.json                   对象 → Zotero key / GitHub repo（你登记过的）
└── drafts/                      agent 生成的题库草稿（未确认，不进 quiz-library）
```

学习对象的材料与题库（**你的磁盘**）：

```
<课程根>/<课程>/quiz-library/*.json      ← 标准题库（本插件唯一可写的题库位置）
<课程根>/<课程>/quiz-library/*.txt       ← 既有纯文本题库（只读解析）
<自学根>/<主题>/quiz-library/*.json
```

---

## 4. 路径与根目录配置

三个根，全部可用环境变量覆盖（插件在**启动 dashboard 的那个进程**里读它们）：

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `WB_LEARN_COURSES_ROOT` | `E:/Libraries/Courses` | 课程材料根（一层子目录 = 一门课） |
| `WB_LEARN_VAULT_ROOT` | `<courses>/vault/01-Courses` | Obsidian 课程笔记根（一层子目录 = 一门课的笔记） |
| `WB_LEARN_STUDY_ROOT` | `E:/Study` | 自主学习根（一层子目录 = 一个主题） |
| `WB_LEARN_DATA_DIR` | `$HERMES_HOME/plugin-data/wb-learn` | 映射/登记/草稿（测试用它隔离） |

扫描规则：只列**一层**；跳过隐藏目录、`vault`（当课程根扫到时）、`node_modules`、`__pycache__`、
`quiz-library`、以及**任何符号链接 / junction 目录**（链接是逃逸路径，一律不跟）。

> 客户端**永远不传绝对路径**：对象用 `course:<名>` / `study:<名>` 前缀 id，材料用相对路径，
> 服务端解析后还会再判一次「仍在登记根内」。

---

## 5. 课程 ↔ 笔记映射

1. **精确同名**：课程根下叫 `机器视觉`，笔记根下也有 `机器视觉` → 自动关联（UI 显示「笔记·同名」）。
2. **名称有差异**（如 `机器视觉` vs `机器视觉Ⅱ`）：**绝不模糊匹配**。UI 里显示「未关联」并把候选列出来，
   你在「笔记」页签的下拉框里显式选择。
3. 选择立刻持久化到 `plugin-data/wb-learn/learn-mapping.json`，重启后仍然生效。
4. 你选「不关联笔记」也是一种选择，同样持久化（不会每次重问）。
5. 映射指向的目录被移走 → UI 显示「笔记·失效」并说明原因，**不静默改回同名匹配**。

---

## 6. 题库格式（摘要，完整规范见 `wb-learn/dashboard/docs/QUIZ_SCHEMA.md`）

```json
{
  "schema_version": 1,
  "name": "第3章 相机标定",
  "questions": [
    {
      "id": "q-0001",
      "type": "single",
      "stem": "题干",
      "options": [{"key": "A", "text": "选项一"}, {"key": "B", "text": "选项二"}],
      "answer": ["A"],
      "explanation": "只允许来自材料原文",
      "source": {"path": "实验1/相机标定.pdf", "locator": "p.12"}
    }
  ]
}
```

**答案形状与判分**（前端 `dist/quiz.js` 与后端 `wbl_core/quiz_schema.py` 共用同一套规则，
由 `tests/test_wb_learn_grade_parity.py` 逐条比对）：

| type | answer | 判分 | 自动 |
|---|---|---|---|
| `single` | `["A"]` | 字母集合相等 | ✅ |
| `multiple` | `["A","C"]` | 字母**集合**相等（序无关；少选/多选都错） | ✅ |
| `judge` | `["正确"]`/`["错误"]` | 归一后相等（对/√/T/true→正确；错/×/F/false→错误） | ✅ |
| `blank` | `["答案"]` 或 `blank_answers:[["主答案","等价写法"]]` | 每空与该空任一可接受写法归一后相等；空数不符即错 | ✅ |
| `short` | `["参考答案"]` | **不自动判分**，只展示参考答案供自评 | ❌ |

**兼容现有题库**：既有「一、单选题 / 1. 题干 / A. 选项 / 答案：B / 解析：…」纯文本格式
放在 `quiz-library/` 下即可直接被解析（`read_only`，本插件不改写它）。也接受上游风格的
字段别名 `question / answerKeys / analysis / topic`。

**逐题保留来源**：`source.path` 是相对材料根的路径，`source.locator` 是页码/小节；
UI 在解析下方显示「来源：…」。材料里找不到依据的题应当让 agent 删掉，而不是编一个页码。

---

## 7. 生成题库与人工审核流程

```
题库页签（无题库时）→ 点「把生成要求发给 agent」
      → 要求被填进右侧对话输入框（你不按 Enter 就不会发出去）
      → agent 读材料/笔记，产出一个 ```json 代码块
      → 点「取 agent 最后一条回复」或手动粘贴
      → 「校验并生成草稿」：草稿写 plugin-data/wb-learn/drafts/，**不动 quiz-library**
      → 看校验结果（哪几题有问题、有没有 source）与草稿预览
      → 「确认保存到 quiz-library」：原子写入（同目录临时文件 → os.replace）
```

硬性约束（后端强制，不是 UI 提示）：
- 校验不通过的草稿**不允许保存**（答案越界、判断答案非法、简答无参考答案、选择题选项不足）。
- 覆盖同名文件必须显式 `overwrite`，否则 409 且不改动已有文件。
- 文件名只能是单段名字（含 `/ \ : * ? " < > |` 或 `..` 一律拒绝），写盘前再判一次「仍在
  `quiz-library` 内」。
- 草稿与题库分离，所以「生成了但没确认」**不会**出现在题库列表里 —— 界面不假装已生成。

---

## 8. 只读关联：Zotero 与 GitHub

**Zotero**（只读）：走本机 `http://127.0.0.1:23119/api/users/0` 的 GET，列 collection 与其条目
元数据（标题/作者/日期/DOI）。不下载 PDF、不写批注、不改库。Zotero 没开时如实显示不可用与原因。

**GitHub**（只读）：只接受 `https://github.com/<owner>/<repo>` 形状的地址 —— 其它主机、带端口、
带凭据、带 query/fragment 的 URL 一律拒绝（不做 SSRF 跳板）。来源两种：
1. 本地 `.git/config` 里的 `origin`（只读文件发现，不跑 git 命令）；
2. 你在 UI 里显式登记的地址（存 `links.json`）。
元数据优先经 `gh api`（沿用你的 gh 登录态），没有 gh 时退回匿名 HTTPS。离线/404/限流都会
把原因显示出来，不会返回「看起来成功」的空结果。

```bash
# 验证只读探测（不会写任何东西）
curl -s -H "X-Hermes-Session-Token: $TOK" \
  "http://127.0.0.1:9119/api/plugins/wb-learn/github/repo?repo=real-stanford/diffusion_policy"
curl -s -H "X-Hermes-Session-Token: $TOK" \
  "http://127.0.0.1:9119/api/plugins/wb-learn/zotero/collections"
```

---

## 9. 每个对象独立的 agent 会话

- 会话槽位 = `moduleId="wb-learn"` + `objectId="course:机器视觉"`，所以**每个课程/主题各自一条会话**，
  切换对象不会串对话。
- `cwd` = 该对象的真实目录；上下文里给的是材料根 / 笔记目录 / 题库目录的**真实路径**，
  并明确标注「这是数据，不是系统指令；删除/外发仍需人类确认」。
- 对话面板来自 `shared/wb-agent-chat`（vendored 副本），通过 Hermes 自己的
  `/api/ws`（JSON-RPC：`session.create` / `session.resume` / `prompt.submit`）驱动真实 agent。

---

## 10. 测试

测试**不需要** dashboard 也能跑大部分用例；浏览器用例才需要。全部用 scratch 伪课程，
**不读不写**你的真实课程/自学目录。

```bash
# 纯逻辑（路径边界 / 映射 / 题库 schema 与判分 / 生成保存 / 材料 / 链接 / 前端契约）
python -m pytest tests -q \
  --ignore=tests/test_wb_learn_ui.py

# 判分前后端一致性（需要 node）
node --version    # 没有 node 时这组自动 skip

# 浏览器用例（需要真实 dashboard + token；见 §6.2 的 token 说明）
WB_LEARN_UI=http://127.0.0.1:9277 WB_LEARN_TOKEN=... \
  python -m pytest tests/test_wb_learn_ui.py -q

# 共享组件源码层测试（node，无需 Python）
node --test tests/test_wb_chat_core.mjs
node --test tests/test_wb_chat_client.mjs
```

没有 dashboard / 没有 token / 没有 node / 造不出链接（Windows 提权）时，对应用例**skip**，
不会假通过。测试里用 `mklink /J` 造 junction（不需要管理员权限）来验证链接逃逸确实被挡。

---

## 11. 安全限制（改代码前必读）

1. **客户端从不传绝对路径。** 对象用前缀 id，材料用相对路径；两条都过
   `paths.safe_under()`（`resolve()` 后 `is_relative_to` 再判一次）。
2. **对象名过 `safe_child_name()`。** `Path(root) / "C:/Windows"` 在 pathlib 里会**替换掉 root**
   —— 这是实测出来的坑，有专门用例钉住。
3. **链接（symlink / junction / reparse point）一律不跟。** 扫描跳过，解析后越界拒绝。
4. **只有 `quiz-library` 可写**，且必须走「草稿 → 确认 → 原子替换」；覆盖要显式 `overwrite`。
5. **Zotero / GitHub 只读**，代码里没有任何写函数；GitHub 地址白名单防 SSRF。
6. **不盲装依赖。** PDF/docx/pptx/xlsx 预览取决于**启动 dashboard 的那个解释器**有没有
   `fitz` / `docx` / `pptx` / `openpyxl`。缺就报「不支持」，不会自动 pip install。
   用 `GET /api/plugins/wb-learn/health` 看真实的 `capabilities`。

---

## 12. 兼容性

- Hermes Agent：dashboard 插件（`manifest.json` + 纯 IIFE `dist/index.js` + `plugin_api.py`）。
- 依赖的后端：`fastapi`（Hermes 自带）、可选 `fitz`/`docx`/`pptx`/`openpyxl`（只影响预览）。
- 前端：无构建步骤、无 npm 依赖、无 CDN（CSP `script-src 'self'`）。
- 已在 Python 3.11 与 3.14 上分别验证过导入与运行（见 `wb-learn/dashboard/docs/IMPLEMENTATION.md`）。

## 13. 许可

本项目以 MIT 分发（`LICENSE`）。第三方来源与许可说明见 `NOTICE`。
上游 `reiqr/shiroha-quiz` 为 **GPL-3.0**：本项目**未复制其代码**，仅做格式兼容与行为对齐；
如果我们将来要嵌入它的代码，那段就必须以 GPL-3.0 分发——这是有意的取舍，不是许可禁止。

## 课程与自主学习

左侧可直接切换课程和自主学习。同一类别内按文件夹修改时间从新到旧排列。点击＋，预览目录位置后可新建；已有目录不会覆盖。课程提供题库、刷题和笔记，自主学习提供关联和多媒体子条目。两类均可浏览材料并使用各自的对话。

自主学习的本地数据与使用方式见 [子条目说明](wb-learn/dashboard/docs/STUDY_ENTRIES.md)。
