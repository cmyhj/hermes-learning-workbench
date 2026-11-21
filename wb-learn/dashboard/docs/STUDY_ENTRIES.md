# 自主学习子条目 · 数据格式与接口

> 适用：`wb-learn` 学习模块（Hermes dashboard 插件）。本文只描述**自主学习**（`study:`）对象；
> 课程对象（`course:`）没有子条目，界面上会明确提示不适用。

## 1. 概念

一个自主学习项目（对应磁盘上 `E:\Study\<主题>` 或 `WB_LEARN_STUDY_ROOT/<主题>`）下有多个
**子条目**；每个子条目是一组**媒体项**，可混合多类：

| item `type` | 含义 | 主要字段 |
|---|---|---|
| `zotero` | 一篇 Zotero 文献 | `itemKey`（8 位大写）、`title` |
| `repo` | 一个代码仓库 | `provider`（`github` / `huggingface`）、`url`、`owner`、`repo`、`local_path`、`cloned_at`、`branch` |
| `videos` | 一个视频链接 | `url`、`title` |
| `websites` | 一个网站链接 | `url`、`title` |

同一子条目可以同时含多个不同类型项（例如「DQN 主线」里放 1 篇论文 + 1 个代码仓库 + 2 个视频）。

## 2. 文件位置

全部在**插件自己的数据目录**，不写入用户笔记/课程资料：

```
$HERMES_HOME/plugin-data/wb-learn/
├── study-entries.json      # 子条目
└── learning-records.json   # 点击学习记录
```

- 写入是原子的（临时文件 + `os.replace`），不会留下半截文件。
- 文件损坏时会被重命名为 `*.json.bak` 并**报错**（不静默清空），可人工恢复。
- 目录可用环境变量 `WB_LEARN_DATA_DIR` 覆盖（测试用）。

### 2.1 `study-entries.json`

```jsonc
{
  "study:RL教学": {
    "entries": [
      {
        "id": "eff749f038a",
        "title": "DQN 主线",
        "note": "先看 EasyRL 第 4~6 章",       // 可选
        "created_at": "2026-11-22T03:09:53",
        "updated_at": "2026-11-22T03:09:53",
        "items": [
          { "id": "i1a2b3c4d5e", "type": "zotero", "itemKey": "ABCD1234", "title": "Playing Atari with DRL" },
          { "id": "i6f7g8h9i0j", "type": "repo", "provider": "github",
            "url": "https://github.com/datawhalechina/easy-rl",
            "owner": "datawhalechina", "repo": "easy-rl",
            "local_path": "", "cloned_at": null },
          { "id": "ik1l2m3n4o5", "type": "videos", "url": "https://www.bilibili.com/video/BV1", "title": "李宏毅 RL" },
          { "id": "ip6q7r8s9t0", "type": "websites", "url": "https://hrl.boyuai.com" }
        ]
      }
    ]
  }
}
```

### 2.2 `learning-records.json`

```jsonc
{
  "study:RL教学": {
    "items":   { "<item_id>":   { "count": 3, "first_at": "...", "last_at": "...", "type": "websites" } },
    "entries": { "<entry_id>":  { "count": 5, "first_at": "...", "last_at": "..." } },
    "daily":   { "2026-11-22": 8 }
  }
}
```

点击计数在进程内加锁**原子自增**（40 线程并发不丢）。时间格式 `YYYY-MM-DDTHH:MM:SS`（本地时区）。

## 3. 接口

全部挂在 `/api/plugins/wb-learn/`，需 dashboard 会话鉴权。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/study/entries?id=<object_id>` | 列出子条目 |
| POST | `/study/entries` | 新建（无 `entry_id`）或更新；`items` 整体替换，`add_items` 追加 |
| DELETE | `/study/entries?id=&entry_id=&item_id?&confirm=true` | 删子条目或单项；**必须** `confirm=true` |
| POST | `/study/items/resolve-repo` | 由 Zotero 文献搜 GitHub 候选（只读，绝不自动下载） |
| POST | `/study/items/clone-repo` | 用户确认后克隆到 `<对象根>/code/<owner>__<repo>` |
| POST | `/study/items/open` | 用 VSCode 打开已克隆仓库 |
| POST | `/study/items/click` | 记一次点击（学习记录） |
| GET | `/study/records?id=<object_id>` | 汇总：总点击、按类型、最近 7/30 天、每条目/每项次数、最近访问 |

### 3.1 找仓库与克隆

1. `resolve-repo` 拿该 Zotero 条目的标题/摘要/DOI，用 `gh search repos` 搜候选并按相关性排序返回，
   **只给候选**（含 stars、描述、来源）。找不到时返回 `not_found` 与已尝试的查询串。
2. 用户点选候选后调 `clone-repo`。目标目录 = `<study 对象根>/code/<owner>__<repo>`：
   - 已存在 → 返回 `status=exists`，**不覆盖**；
   - 先走代理 `http://127.0.0.1:7890`，失败重试一次直连；
   - 成功后回读 `.git` 校验，并把 `local_path`/`cloned_at` 写回该 item。

### 3.2 打开行为（前端）

| 项类型 | 行为 |
|---|---|
| 论文 | `window.open("zotero://select/library/items/<itemKey>")` |
| 仓库·已下载 | `POST /study/items/open` → 后端用 VSCode 打开 |
| 仓库·未下载 | `window.open(repo.url)` |
| 视频 / 网站 | `window.open(url)` |

每次点击**先**调 `/study/items/click` 记账，再执行动作；记账失败不阻止跳转，但界面要提示。

## 4. 安全边界（改代码时必须保持）

- 子条目只接受 `study:` 前缀对象；课程对象直接拒绝。
- Zotero key 必须是 8 位大写字母数字；仓库 host 只接受 `github.com` / `huggingface.co`；
  链接只接受 `http(s)` 且不允许带凭据。
- 克隆目标必须落在 `<对象根>/code/` 内；`open` 只接受对象根内的 realpath 且必须是 git 仓库（或 HF 标志）。
- VSCode 以**参数列表**调用（`subprocess.run([...])`），不使用 `shell=True`，不拼接命令字符串。
- 搜索阶段绝不自动选定、绝不自动克隆；删除必须显式 `confirm=true`。
- Zotero 全程只读；插件不写用户的笔记、课程资料或 `.pm`。

## 5. 已知限制

- 点击记录是本地计数，没有去重窗口：同一项连点 N 次就是 N 次。
- `resolve-repo` 依赖本机 `gh` 已登录与网络；离线时如实报错，不返回假候选。
- HuggingFace 仓库的「已下载」判定依赖 `.git` 或 `.hf` 标志；纯文件夹不会被视为已下载。
- 子条目不参与 Zotero 或 GitHub 的任何写操作。
