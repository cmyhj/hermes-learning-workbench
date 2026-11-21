# 上游评估：reiqr/shiroha-quiz

评估目的是回答一个问题：**能不能复用它来实现刷题，怎么复用才不越界。**

- 仓库：<https://github.com/reiqr/shiroha-quiz>
- 本地只读检出：`D:/DevTool/shiroha-quiz`（`git clone --depth 1`，未修改、未提交）
- 检出时 HEAD：`2d29d6a8eb25a62b0e07a3aded3fded7b2b0193d`（2026-11-17，`chore: 发布 v2.8.12.1-beta`）
- remotes：`origin https://github.com/reiqr/shiroha-quiz.git`（仅 fetch/push 地址，本次未推送）

## 1. 许可结论

`LICENSE` 是 **GNU GPL v3.0**（文件首行为 `GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007`）。

**GPL 是允许复用的**——它不是「禁止使用」的许可。约束在于 **copyleft 的传染性**：
一旦本项目包含/链接它的代码并对外分发，整个衍生作品就必须以 GPL-3.0 分发并附完整源码。
这对一个想用 MIT 发布的个人工作台插件是不可接受的代价。

所以这里是**选择**而不是被迫：

* 我们**选择不复制**它的源码，从而不必让整个仓库转为 GPL-3.0。这是产品决策，不是法律禁令。
* 我们**可以**独立学习它的**题库格式**与**判分语义**再自己实现：格式与协议属于事实性信息，
  受版权保护的是具体表达（代码）。
* 如果将来确实想嵌入它的代码，可行路径是：那一部分（或整个仓库）以 GPL-3.0 分发、
  附完整许可与源码。这属于明确的产品决策，本次未采用。
* 只借用了「字段叫什么、判分怎么算」这类事实；`docs/QUIZ_SCHEMA.md` 是本项目自写的规范，
  没有复制上游文档段落。

**本次方式：格式兼容 + 行为对齐，零代码复制。** 仓库里不存在任何来自 shiroha-quiz 的源文件；
`shared/` 与本插件 `dist/` 全部由本项目自行实现。许可与来源说明见 `NOTICE`。

## 2. 实际读到的实现（用于判断哪些值得对齐）

读的是 `apps/web/app.js`（9536 行）与 `apps/web/question-bank.js`（内置示例题库），
以及 `docs/标准题库格式示例/标准题库格式示例.md`。

| 观察到的事实 | 位置 | 本项目的处理 |
|---|---|---|
| 题型枚举 `single / multiple / judge / blank / short`（另有 `cloze` 完形） | `app.js` `TYPE_LABEL` | 采纳前 5 种；**不实现 `cloze`**（逐空标签属于另一个交互模型，schema v1 明确排除） |
| 题目对象字段 `question / options[{key,text}] / answerKeys / answer / analysis / typeLabel / topic / score` | `app.js` 规范化段、`question-bank.js` | 采纳 `question/options/answerKeys/analysis` 作为**兼容输入别名**；自身 schema 用 `stem/options/answer/explanation` |
| 多选按**集合**比对（`same(chosen, answer)`），少选即错 | `app.js:7805` `sameAnswerForQuestion` | 一致。`grade()` 用集合相等，测试覆盖乱序/少选/多选 |
| 判断题答案存 `正确/错误`，且接受 `对/错` 等写法后归一 | `app.js` `judgeToKey` / `isRawJudgeSymbolAnswer` | 一致，但归一表是本项目自己列的（`quiz_schema._TRUE_WORDS`） |
| 填空题多空用 `blankAnswers: [[可接受写法…]]`，且逐空比对 | `app.js` `isMultiBlankQuestionV58914` / `normalizeBlankAnswersV58914` | 采纳「每空一组可接受写法」这一**数据形状**，字段名用 `blank_answers`；比对逻辑自写 |
| 简答题（`short`）只展示参考答案，不自动判分 | `app.js` `feedbackAnswerHtmlV58914` | 一致：`grade()` 返回 `judged=false`，UI 显示「需自评」 |
| 纯文本题库：`一、单选题` 分区 + `1. 题干` + `A. 选项` + `答案：X` + `解析：…` | `docs/标准题库格式示例/*.md` | 已有题库就是这种格式，`parse_text_bank()` 按同一结构解析（自写实现），不改写原文件 |
| 导入时对不合格题会给出 `issues`（答案超出选项、选项过少等） | `app.js:525-540` `validate…` | 采纳这一**行为**：`validate_bank()` 返回逐题 issues，不静默收下坏题 |
| Web 版是**单页 + LocalStorage**、无后端、无 agent 接口 | `apps/web/*` | 不采纳架构：本项目必须是「Hermes agent 在环」的 dashboard 插件 |

## 3. 为什么不做 iframe / 网页接入

事实层面（可核对的两条）：
* 它的 Web 版是纯静态 SPA，题库存 `localStorage`，**没有 HTTP API**。这意味着无法把
  「本对象的 quiz-library」喂给它，也无法把作答结果带回 Hermes —— 这是**功能**上的不匹配，
  与 CSP 无关。
* dashboard 的 CSP 是 `script-src 'self' blob: …`。**CSP 不是 iframe 的准入规则**：
  iframe 是否可嵌由 `frame-ancestors`（本页为被嵌方时）/ `frame-src`（本页嵌别人时）决定，
  而 Hermes 当前的 CSP 指令集里没有对 `frame-src` 的额外限制。所以「CSP 挡住了 iframe」
  是一个**未经验证的断言，本文不作为理由**。真正拦住跨域 iframe 方案的是它自己的
  X-Frame-Options / 服务端配置（上游具体响应头本次未逐条探测，故不做结论）。

产品层面的理由足够充分，不需要依赖上面的不确定性：
* 题目必须来自**本对象的真实材料**，生成必须由当前 Hermes agent 完成；
  一个自带题库、localStorage 存储的第三方 SPA 无法满足这条。
* 把整包当同源静态资源分发，等于分发 GPL 代码（见 §1 的 copyleft 代价）。

结论：**借鉴格式与判分语义，自己实现做题 UI 与生成闭环**（`dist/quiz.js` +
`dist/views.js` + `dist/quizpanel.js` + `wbl_core/quiz_schema.py`）。

## 4. 本项目的原创部分（与上游无关）

* 学习对象模型（课程 / 自主学习 + Obsidian 笔记精确同名关联 + 显式映射持久化）
* 材料只读清单与文本预览（含格式能力如实声明）
* 题库草稿 → 校验 → 用户确认 → 原子写入 `quiz-library` 的闭环
* 每个对象独立 Hermes 会话（vendored `shared/wb-agent-chat`）
* 只读 Zotero / GitHub 关联（地址白名单，防 SSRF）
* 前后端判分一致性测试（同一张题表跑两份实现）

## 5. 如果上游将来改许可

需要在 `NOTICE` 里补一段，并重新评估。当前结论：**GPL-3.0，不复制代码，仅做格式兼容**。
