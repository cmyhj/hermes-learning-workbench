"""wb-learn 只读外链：GitHub 白名单校验（防 SSRF）、Zotero 只读、离线可见。

要求原文：不能任意 URL SSRF、错误/离线可见、不能改 Zotero、不能推 GitHub。
这里既测**拒绝**（危险 URL 一律拒）也测**如实报错**（离线时不返回假成功）。
"""

from __future__ import annotations

import json

import pytest

from wb_learn_support import scratch  # noqa: F401


# ---------------------------------------------------------------- GitHub URL 白名单
@pytest.mark.parametrize("url,expected", [
    ("https://github.com/owner/repo", ("owner", "repo")),
    ("https://github.com/owner/repo.git", ("owner", "repo")),
    ("https://github.com/owner/repo/", ("owner", "repo")),
    ("https://github.com/nuaa-rm/ckyf_vision", ("nuaa-rm", "ckyf_vision")),
    ("https://github.com/real-stanford/diffusion_policy", ("real-stanford", "diffusion_policy")),
])
def test_valid_github_urls_are_accepted(scratch, url, expected):
    from wbl_core import links
    assert links.parse_github_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo",                  # 非 https
    "https://gitlab.com/owner/repo",                 # 其它主机
    "https://github.com.evil.com/owner/repo",        # 后缀伪装
    "https://github.com@evil.com/owner/repo",        # 凭据伪装
    "https://user:pass@github.com/o/r",              # 带凭据
    "https://github.com:8443/owner/repo",            # 带端口
    "https://github.com/owner/repo?ref=main",        # 带 query
    "https://github.com/owner/repo#readme",          # 带 fragment
    "https://127.0.0.1:23119/api/users/0",           # 本机其它服务（SSRF）
    "https://github.com/owner",                      # 只有 owner
    "https://github.com/a/b/c",                      # 多段路径
    "file:///etc/passwd",
    "ftp://github.com/o/r",
    "",
    None,
])
def test_dangerous_urls_are_refused(scratch, url):
    from wbl_core import links
    assert links.parse_github_url(url) is None


def test_saving_non_github_url_is_refused(scratch):
    scratch.study_topic("主题A")
    api = scratch.api()
    from wbl_core import paths as P
    with pytest.raises(P.PathNotAllowed):
        api.links.save_object_links("study:主题A", github="https://evil.example.com/a/b")
    with pytest.raises(P.PathNotAllowed):
        api.links.save_object_links("study:主题A", github="http://github.com/a/b")
    assert scratch.read_links() == {}, "被拒绝的登记不能落盘"


def test_saving_valid_github_repo_persists(scratch):
    scratch.study_topic("主题A")
    api = scratch.api()
    api.links.save_object_links("study:主题A", github="https://github.com/owner/repo")
    stored = scratch.read_links()
    assert stored["study:主题A"]["github"]["repo"] == "owner/repo"
    assert stored["study:主题A"]["github"]["source"] == "manual"

    # 读回：resolve 走登记值
    resolved = api.links.resolve_github("study:主题A")
    assert resolved["repo"] == "owner/repo"
    assert resolved["source"] == "manual"


def test_local_git_remote_is_discovered_read_only(scratch):
    scratch.study_topic("dp", {".git/config":
        '[remote "origin"]\n\turl = git@github.com:real-stanford/diffusion_policy.git\n'})
    api = scratch.api()
    resolved = api.links.resolve_github("study:dp")
    assert resolved["local_remote"] == "git@github.com:real-stanford/diffusion_policy.git"
    assert resolved["repo"] == "real-stanford/diffusion_policy"
    assert resolved["source"] == "git-remote"
    # 本地 .git/config 不被改写
    cfg = (scratch.study / "dp" / ".git" / "config").read_text(encoding="utf-8")
    assert "git@github.com:real-stanford/diffusion_policy.git" in cfg


def test_local_git_remote_of_other_host_is_not_used(scratch):
    scratch.study_topic("x", {".git/config":
        '[remote "origin"]\n\turl = https://gitlab.com/a/b.git\n'})
    api = scratch.api()
    resolved = api.links.resolve_github("study:x")
    assert resolved["local_remote_accepted"] is False
    assert resolved["repo"] is None, "非 github.com 的 origin 不能被采纳"


# ---------------------------------------------------------------- Zotero
def test_zotero_offline_reports_error_not_empty_success(scratch, monkeypatch):
    scratch.study_topic("主题A")
    api = scratch.api()
    from wbl_core import links
    monkeypatch.setattr(links, "ZOTERO_BASE", "http://127.0.0.1:1/api/users/0")
    monkeypatch.setattr(links, "ZOTERO_TIMEOUT", 0.5)

    out = api.zotero_collections()
    assert out["available"] is False
    assert out["error"] and "不可达" in out["error"]
    assert out["collections"] == []

    payload = api.get_links(id="study:主题A")
    assert payload["zotero"]["available"] is False
    assert payload["zotero"]["error"], "离线原因必须在 /links 里可见"


def test_zotero_collection_key_shape_is_enforced(scratch):
    scratch.study_topic("主题A")
    api = scratch.api()
    from wbl_core import paths as P
    for bad in ("abc", "abcdefghij", "abcd123", "../../etc"):
        with pytest.raises(P.PathNotAllowed):
            api.links.save_object_links("study:主题A", zotero=bad)


def test_zotero_collection_must_exist_when_api_reachable(scratch, monkeypatch):
    scratch.study_topic("主题A")
    api = scratch.api()
    from wbl_core import links
    monkeypatch.setattr(links, "zotero_collections", lambda: {
        "available": True, "error": None, "base": "x",
        "count": 1, "collections": [{"key": "ABCD1234", "name": "某集合", "parent": None, "items": 3}],
    })
    from wbl_core import paths as P
    with pytest.raises(P.PathNotAllowed):
        api.links.save_object_links("study:主题A", zotero="ZZZZ9999")
    saved = api.links.save_object_links("study:主题A", zotero="ABCD1234")
    assert saved["links"]["zotero"]["collection"] == "ABCD1234"


def test_clearing_links_works(scratch):
    scratch.study_topic("主题A")
    api = scratch.api()
    api.links.save_object_links("study:主题A", github="https://github.com/a/b")
    assert "github" in scratch.read_links()["study:主题A"]
    api.links.save_object_links("study:主题A", github=None)
    assert "github" not in scratch.read_links()["study:主题A"]


def test_links_are_read_only_flagged(scratch):
    scratch.study_topic("主题A")
    api = scratch.api()
    payload = api.get_links(id="study:主题A")
    assert payload["read_only"] is True
    assert "不写 Zotero" in payload["policy"]
    assert "不 push" in payload["policy"]


def test_unknown_object_cannot_get_links(scratch):
    api = scratch.api()
    with pytest.raises(api.HTTPException) as ei:
        api.get_links(id="study:根本没这个")
    assert ei.value.status_code == 403


# ---------------------------------------------------------------- 真实只读探测（可离线）
@pytest.mark.parametrize("repo", ["real-stanford/diffusion_policy", "nuaa-rm/ckyf_vision"])
def test_real_github_repo_is_read_only_metadata(scratch, repo):
    """只有网络可用时才真正断言；离线时必须报 ok=False 而不是假成功。

    ``license`` 允许为 None：有的仓库确实没有许可（这对「能不能复用代码」是结论性信息，
    不能为了让断言好看而填一个默认值）。
    """
    from wbl_core import links
    owner, name = repo.split("/")
    out = links.github_repo(owner, name)
    if not out["ok"]:
        assert out["error"], "失败必须带原因"
        pytest.skip(f"GitHub 不可达：{out['error']}")
    assert out["repo"] == repo
    assert out["url"].startswith("https://github.com/")
    assert out["read_only"] is True
    assert "license" in out
    if out["license"] is not None:
        assert isinstance(out["license"], str) and out["license"]
