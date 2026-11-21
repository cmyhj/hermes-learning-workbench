# 题库格式（schema v1）与兼容范围

本文件是 wb-learn 题库格式的**唯一规范**。字段名、判分规则、兼容范围都在这里，
`wbl_core/quiz_schema.py` 是它的实现，`tests/test_wb_learn_quiz.py` 是它的证据。

## 1. 存放位置（固定，不迁移你已有的题库）

```
<学习对象目录>/quiz-library/<任意名字>.json     ← 标准格式（本 schema）
<学习对象目录>/quiz-library/<任意名字>.txt      ← 既有纯文本题库（只读解析）
```

* 课程对象的 `<学习对象目录>` = `$WB_LEARN_COURSES_ROOT/<课程>`（默认 `E:/Libraries/Courses/<课程>`）
* 自主学习对象 = `$WB_LEARN_STUDY_ROOT/<主题>`（默认 `E:/Study/<主题>`）
* `quiz-library` 这个**相对路径是固定的**，不提供配置项，也不改写你已有的文件。
* 这是**唯一**允许写入题库的目录。`.txt` 只读：解析出题目用于练习，但不在本工具里改写。

## 2. 整库结构

```json
{
  "schema_version": 1,
  "name": "机器视觉 · 第3章",
  "object_id": "course:机器视觉",
  "generated": { "by": "hermes-agent", "at": "2026-11-21T12:00:00", "model": null },
  "questions": [ ... ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 建议 | 只能是 `1`。缺省视为 1；写成别的值会判为不合格（不猜版本） |
| `name` | 否 | 缺省用文件名 |
| `object_id` | 否 | 生成时写入；保存时以**保存目标对象**为准，不会因文件里写错而串对象 |
| `generated` | 否 | `by` / `at` / `model`。保存时会补齐 `by=hermes-agent` 与 `at` |
| `questions` | **是** | 非空数组 |

也接受「顶层就是题目数组」的最简写法（`[ {...}, {...} ]`）。

## 3. 单题结构

```json
{
  "id": "q-0001",
  "type": "single",
  "stem": "题干",
  "options": [{ "key": "A", "text": "选项一" }],
  "answer": ["A"],
  "blank_answers": [["主答案", "等价写法"]],
  "explanation": "只允许来自材料原文",
  "source": { "path": "相对材料根的路径", "locator": "p.12 / 第 3 节" },
  "points": 1,
  "tags": []
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是（可推断） | `single` / `multiple` / `judge` / `blank` / `short`。缺失时只在**结构证据明确**时推断（有选项+单字母答案→single 等），推断不出来直接报错，不猜 |
| `stem` | **是** | 题干非空 |
| `options` | 选择题必填 | `[{"key":"A","text":"..."}]`，key 只允许 A–H，至少 2 个；也接受 `{"A":"文本"}` 映射写法 |
| `answer` | 是 | 见下表「答案形状」 |
| `blank_answers` | 填空题建议 | 每空一组**可接受写法**；缺省时由 `answer` 用 `;`／`；` 切分 |
| `explanation` | 否 | 解析。**只能来自材料原文**，没有就留空，禁止编造 |
| `source` | 建议 | `{path, locator}`。`path` 是相对材料根的路径。没给就是 `null`，不会被填成猜测值 |
| `points` | 否 | 默认 1 |
| `tags` | 否 | 字符串数组 |

### 答案形状与判分（实现严格按这张表）

| type | answer 形状 | 判分规则 | 自动判分 |
|---|---|---|---|
| `single` | `["A"]` | 选项字母集合完全相等（大小写归一） | ✅ |
| `multiple` | `["A","C"]` | 选项字母**集合**相等：顺序无关，**少选/多选都判错** | ✅ |
| `judge` | `["正确"]` / `["错误"]` | 归一后相等：`正确/对/是/√/T/true/1` → 正确；`错误/错/否/×/F/false/0` → 错误 | ✅ |
| `blank` | `["答案"]` 或配 `blank_answers` | 每个空与「该空任一可接受写法」归一后相等（去空白与中英文标点、转小写）；**空数不符直接判错** | ✅ |
| `short` | `["参考答案"]` | **不自动判分**：只展示参考答案，由用户自评 | ❌（不做关键词假判） |

前端（`dist/quiz.js`）与后端（`wbl_core/quiz_schema.py`）各有一份实现，由
`tests/test_wb_learn_grade_parity.py` 用同一组固定题逐条比对，漂移即测试失败。

## 4. 校验与保存流程

```
agent 生成 JSON → POST /quiz/drafts（草稿，写 plugin-data，不进 quiz-library）
      → 校验结果（issues / warnings / 预览）
      → 用户点「确认保存」→ POST /quiz/drafts/{id}/commit（原子写 quiz-library）
```

* **校验不通过的草稿不允许保存**（`ok=false` → 422）。答案越界、判断答案非法、
  简答题缺参考答案、选择题选项不足，都算不合格。
* 覆盖同名文件必须在请求里带 `overwrite: true`；否则 409，且不改动已有文件。
* 文件名只能是单段名字：含 `/ \ : * ? " < > |` 或 `..` 一律 403；写盘前还会再判一次
  「解析后仍在 `quiz-library` 内」。
* 保存先写同目录临时文件再 `os.replace`，中途失败不会留下半个题库。
* 草稿存在 `plugin-data/wb-learn/drafts/`，所以「生成了但没确认」的项目**不会**出现在
  题库列表里——界面不会假装已经生成。

## 5. 兼容范围（明确写清楚，不含糊）

**读得懂（自动识别，无需转换）**
* 本 schema 的 JSON；顶层数组写法；`options` 映射写法。
* 上游 Shiroha Quiz 风格的字段命名：`question` / `answerKeys` / `analysis` / `topic` /
  `type: "single"|"multiple"|"judge"|"blank"|"short"`。
* 既有机器视觉题库的纯文本格式：`一、单选题` / `1. 题干` / `A. 选项` / `答案：B` / `解析：…`，
  以及 `二、判断题` / `三、填空题` / `四、简答题` 分节；多空填空用 `；` 分隔。

**读不懂（会明确报错，不会静默丢题）**
* 加密/损坏的 JSON → 题库列表里该文件标 `ok=false` 并给出错误。
* 没有文字层的扫描 PDF → 预览明确提示「不支持文本提取，未做 OCR」。
* 本工具**不支持**的题型（完形填空的逐空标签、图片题的图片绑定）→ 不要写进 schema v1，
  会被判为无法识别。

**不做的事**
* 不迁移、不改写你已有的题库文件（`.txt` 只读；`.json` 只有 `quiz-library` 里你确认过的
  写入才会出现）。
* 不支持从 `.docx/.xlsx/.pdf` 直接批量导入成题库（生成走 agent + 草稿确认）。
* 不伪造 `source`：材料里找不到依据的题应当让 agent 删掉，而不是编一个页码。

## 6. 最小可用例子

见 `examples/quiz-bank.example.json`（3 题，覆盖 single / multiple / judge，带真实 source）。
`tests/test_wb_learn_quiz.py::test_valid_sample_bank_passes` 直接用它做回归。
