"""wb-learn 自主学习子条目 / 一键找仓库 / 点击学习记录 —— 真实运行的后端用例。

铁律：全部用 ``scratch`` 造的临时目录（伪 study 对象、伪 plugin-data），
**不读写** 真实 ``E:/Study`` 或真实 plugin-data；Zotero / gh / git 这些外部命令
在需要处被替换成可审计的假执行器，或使用本地 ``file://`` 仓库做真实 shallow clone。

覆盖的安全边界（每条都对应一个真实缺陷类别）：
* 路径越界（对象根外）、目录穿越、非法 host、非法 zotero key → 必须拒绝；
* 克隆目标已存在 → 不覆盖；
* ``open`` 拒绝对象根外路径与非 git 目录；
* 点击计数并发不丢；
* 损坏的 study-entries.json → 保留 .bak 且报错，不静默清空。
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

import wb_learn_support as S
from wb_learn_support import scratch  # noqa: F401  （pytest fixture）

STUDY_ID = "study:RL教学"


# ---------------------------------------------------------------- 小助手
def _api(scratch):
    return scratch.api()


def _svc(scratch):
    """拿到内核模块（study / records / repo_ops）。"""
    _api(scratch)
    from wbl_core import records, repo_ops, study  # noqa: PLC0415

    return study, records, repo_ops


def _study_topic(scratch, name="RL教学"):
    return scratch.study_topic(name, {"NOTES.md": "# 笔记\n"})


# ---------------------------------------------------------------- 子条目 CRUD
def test_create_list_update_delete_entry(scratch):
    _study_topic(scratch)
    study, _, _ = _svc(scratch)

    entry = study.create_entry(STUDY_ID, "DQN 主线", note="先看 EasyRL")
    assert entry["title"] == "DQN 主线"
    assert entry["items"] == []
    assert entry["id"].startswith("e")

    got = study.get_entries(STUDY_ID)
    assert [e["id"] for e in got] == [entry["id"]]

    updated = study.update_entry(STUDY_ID, entry["id"], title="DQN 与 PPO")
    assert updated["title"] == "DQN 与 PPO"

    study.delete_entry(STUDY_ID, entry["id"])
    assert study.get_entries(STUDY_ID) == []


def test_entry_title_required(scratch):
    _study_topic(scratch)
    study, _, _ = _svc(scratch)
    from wbl_core import paths

    with pytest.raises(paths.PathNotAllowed):
        study.create_entry(STUDY_ID, "   ")


def test_course_object_is_rejected(scratch):
    scratch.course("机器视觉", {"a.md": "x"})
    study, _, _ = _svc(scratch)
    from wbl_core import paths

    with pytest.raises(paths.PathNotAllowed):
        study.create_entry("course:机器视觉", "不该允许")


def test_entries_file_corruption_keeps_bak(scratch):
    _study_topic(scratch)
    study, _, _ = _svc(scratch)
    bad = scratch.data / "study-entries.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ 这不是 JSON", encoding="utf-8")

    with pytest.raises(study.StudyStoreError):
        study.get_entries(STUDY_ID)

    bak = bad.with_suffix(".json.bak")
    assert bak.is_file(), "损坏文件必须留 .bak 供恢复"
    assert not bad.exists()


def test_atomic_write_leaves_no_tmp(scratch):
    _study_topic(scratch)
    study, _, _ = _svc(scratch)
    study.create_entry(STUDY_ID, "A")
    leftovers = [p.name for p in scratch.data.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


# ---------------------------------------------------------------- item 校验
@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"type": "zotero", "itemKey": "abc"}, "zotero key 不是 8 位大写"),
        ({"type": "zotero"}, "缺 zotero key"),
        ({"type": "repo", "provider": "gitlab", "url": "https://gitlab.com/a/b"}, "provider 非白名单"),
        ({"type": "repo", "provider": "github", "url": "https://evil.com/a/b"}, "host 非 github.com"),
        ({"type": "repo", "provider": "github", "url": "http://github.com/a/b"}, "非 https"),
        ({"type": "repo", "provider": "huggingface", "url": "https://huggingface.co/a"}, "HF 路径不足两段"),
        ({"type": "videos", "url": "javascript:alert(1)"}, "链接 scheme 非法"),
        ({"type": "websites", "url": "https://user:pw@x.com"}, "链接带凭据"),
        ({"type": "unknown", "url": "https://x.com"}, "未知类型"),
    ],
)
def test_invalid_items_rejected(scratch, payload, why):
    _study_topic(scratch)
    study, _, _ = _svc(scratch)
    from wbl_core import paths

    with pytest.raises(paths.PathNotAllowed):
        study.normalize_item(payload)


def test_mixed_items_accepted(scratch):
    _study_topic(scratch)
    study, _, _ = _svc(scratch)
    items = [
        study.normalize_item({"type": "zotero", "itemKey": "ABCD1234", "title": "论文"}),
        study.normalize_item({"type": "repo", "provider": "github",
                              "url": "https://github.com/datawhalechina/easy-rl"}),
        study.normalize_item({"type": "repo", "provider": "huggingface",
                              "url": "https://huggingface.co/huggingface/deep-rl-class"}),
        study.normalize_item({"type": "videos", "url": "https://www.bilibili.com/video/BV1", "title": "李宏毅"}),
        study.normalize_item({"type": "websites", "url": "https://hrl.boyuai.com"}),
    ]
    assert [i["type"] for i in items] == ["zotero", "repo", "repo", "videos", "websites"]
    assert items[1]["owner"] == "datawhalechina"
    assert items[1]["repo"] == "easy-rl"
    assert items[1]["local_path"] == ""


# ---------------------------------------------------------------- clone-repo
def _local_repo(tmp_path: Path) -> Path:
    """造一个真实的本地 git 仓库，供 ``file://`` shallow clone 使用。"""
    src = tmp_path / "origin-repo"
    src.mkdir()
    (src / "README.md").write_text("# demo\n", encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e"}
    import os

    run = lambda *a: subprocess.run(["git", *a], cwd=src, check=True, capture_output=True,  # noqa: E731
                                    env={**os.environ, **env})
    run("init", "-q")
    run("add", ".")
    run("commit", "-q", "-m", "init")
    return src


def test_clone_target_already_exists_is_not_overwritten(scratch, monkeypatch):
    _study_topic(scratch)
    study, _, repo_ops = _svc(scratch)
    target = scratch.study / "RL教学" / "code" / "datawhalechina__easy-rl"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("别覆盖我", encoding="utf-8")

    called = []
    monkeypatch.setattr(repo_ops, "_run_git", lambda *a, **k: called.append(a) or (0, "", ""))

    out = repo_ops.clone_repo(STUDY_ID, "github", "https://github.com/datawhalechina/easy-rl")
    assert out["status"] == "exists"
    assert (target / "keep.txt").read_text(encoding="utf-8") == "别覆盖我"
    assert called == [], "目标已存在时不应该执行 git"


def test_clone_uses_real_shallow_clone(scratch, monkeypatch, tmp_path):
    """真实 git clone（file:// 协议）验证：目标路径形状 + .git 回读通过。"""
    _study_topic(scratch)
    _, _, repo_ops = _svc(scratch)
    origin = _local_repo(tmp_path)

    def _real_git(args, cwd=None):
        """把 github URL 换成本地仓库路径，其余参数原样交给真实 git。

        注意：Windows 上 git 对 ``file:///C:/...`` 会解析成 ``/C:/...`` 而失败，
        所以这里用**普通路径**（git 会走本地克隆路径，--depth 被忽略但仍真实执行）。
        """
        swapped = [str(origin) if a == "https://github.com/o/r" else a for a in args]
        proc = subprocess.run(["git", *swapped], cwd=cwd, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    monkeypatch.setattr(repo_ops, "_run_git", _real_git)

    out = repo_ops.clone_repo(STUDY_ID, "github", "https://github.com/o/r")
    assert out["status"] == "ok", out
    got = Path(out["path"])
    assert got == scratch.study / "RL教学" / "code" / "o__r"
    assert (got / ".git").exists()
    assert (got / "README.md").read_text(encoding="utf-8") == "# demo\n"


def test_clone_rejects_outside_object_root(scratch):
    _study_topic(scratch)
    _, _, repo_ops = _svc(scratch)
    from wbl_core import paths

    with pytest.raises(paths.PathNotAllowed):
        repo_ops.clone_repo(STUDY_ID, "github", "https://github.com/../../etc/passwd")


# ---------------------------------------------------------------- open-in-vscode
def test_open_rejects_path_outside_object_root(scratch):
    _study_topic(scratch)
    _, _, repo_ops = _svc(scratch)
    from wbl_core import paths

    outside = scratch.root / "outside-repo"
    outside.mkdir(parents=True)
    (outside / ".git").mkdir()
    with pytest.raises(paths.PathNotAllowed):
        repo_ops.open_in_vscode(STUDY_ID, str(outside))


def test_open_rejects_non_git_dir(scratch):
    _study_topic(scratch)
    _, _, repo_ops = _svc(scratch)
    from wbl_core import paths

    plain = scratch.study / "RL教学" / "code" / "plain"
    plain.mkdir(parents=True)
    with pytest.raises(paths.PathNotAllowed):
        repo_ops.open_in_vscode(STUDY_ID, str(plain))


def test_open_calls_vscode_with_list_args(scratch, monkeypatch):
    _study_topic(scratch)
    _, _, repo_ops = _svc(scratch)
    repo = scratch.study / "RL教学" / "code" / "a__b"
    (repo / ".git").mkdir(parents=True)

    seen = {}

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["shell"] = kwargs.get("shell", False)
        return _Done()

    monkeypatch.setattr(repo_ops, "_find_vscode", lambda: "code")
    monkeypatch.setattr(repo_ops.subprocess, "run", fake_run)

    out = repo_ops.open_in_vscode(STUDY_ID, str(repo))
    assert out["status"] == "ok"
    assert isinstance(seen["cmd"], list) and seen["cmd"][0] == "code"
    assert seen["shell"] is False, "禁止 shell=True"


# ---------------------------------------------------------------- 点击计数
def test_click_counts_increment_and_summarize(scratch):
    _study_topic(scratch)
    study, records, _ = _svc(scratch)
    entry = study.create_entry(STUDY_ID, "A")
    item = study.normalize_item({"type": "websites", "url": "https://a.com"})
    study.update_entry(STUDY_ID, entry["id"], items=[item])

    for _ in range(3):
        records.record_click(STUDY_ID, item["id"], item_type="websites", entry_id=entry["id"])

    summary = records.summarize(STUDY_ID)
    assert summary["total"] == 3
    assert summary["by_type"]["websites"] == 3
    assert summary["items"][item["id"]]["count"] == 3
    assert summary["entries"][entry["id"]]["count"] == 3
    assert summary["last_7d"] == 3


def test_click_count_is_atomic_under_concurrency(scratch):
    _study_topic(scratch)
    study, records, _ = _svc(scratch)
    entry = study.create_entry(STUDY_ID, "A")
    item = study.normalize_item({"type": "videos", "url": "https://v.com"})
    study.update_entry(STUDY_ID, entry["id"], items=[item])

    n = 40
    errors = []

    def worker():
        try:
            records.record_click(STUDY_ID, item["id"], item_type="videos")
        except Exception as exc:  # pragma: no cover - 只在并发失控时触发
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert records.summarize(STUDY_ID)["total"] == n, "并发点击丢失说明自增不是原子的"


def test_click_rejects_object_id_without_study_prefix(scratch):
    """课程对象不能记学习点击（子条目只属于 study: 对象）。"""
    _study_topic(scratch)
    scratch.course("机器视觉", {"a.md": "x"})
    _, records, _ = _svc(scratch)
    from wbl_core import paths

    with pytest.raises(paths.PathNotAllowed):
        records.record_click("course:机器视觉", "i1")
