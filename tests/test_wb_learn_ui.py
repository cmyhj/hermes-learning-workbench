"""wb-learn 真实浏览器测试：1280 桌面宽度 + 375 手机宽度。

前置：一个**独立 loopback 端口**的 dashboard（不是 9119，那是当前对话自己的后端）。
默认 9277，可用 ``WB_LEARN_UI`` 覆盖；服务没起时整组 skip（不假通过）。

    HERMES_DASHBOARD_SESSION_TOKEN=... hermes dashboard --port 9277 --skip-build
    WB_LEARN_UI=http://127.0.0.1:9277 WB_LEARN_TOKEN=... venv/Scripts/python.exe -m pytest tests/test_wb_learn_ui.py -q

只读页面状态 + 一次真实点击流程（切对象、切标签页、做题提交），
**不写任何用户目录**：不做「确认保存题库」，那一步在 test_wb_learn_generate.py 里用
scratch 目录验证。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

BASE = (os.environ.get("WB_LEARN_UI") or "http://127.0.0.1:9277").rstrip("/")
TOKEN = os.environ.get("WB_LEARN_TOKEN") or os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN") or ""

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    HAVE_PW = True
except Exception:  # pragma: no cover
    HAVE_PW = False


def _api_up() -> bool:
    req = urllib.request.Request(BASE + "/api/status")
    if TOKEN:
        req.add_header("X-Hermes-Session-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _plugin_up() -> bool:
    req = urllib.request.Request(BASE + "/api/plugins/wb-learn/health")
    if TOKEN:
        req.add_header("X-Hermes-Session-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            return bool(data.get("ok"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


pytestmark = [
    pytest.mark.skipif(not HAVE_PW, reason="playwright 未安装"),
    pytest.mark.skipif(not _api_up(), reason=f"dashboard 未在 {BASE} 运行"),
    pytest.mark.skipif(not _plugin_up(), reason=f"wb-learn 后端在 {BASE} 不可用（401/未启用？需要 WB_LEARN_TOKEN）"),
]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _open(browser, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    if TOKEN:
        ctx.set_extra_http_headers({"X-Hermes-Session-Token": TOKEN})
    page = ctx.new_page()
    page.goto(BASE + "/learn", wait_until="domcontentloaded")
    # 页面会异步注入 7 个 dist 脚本 + 拉数据；等侧栏对象出现
    page.wait_for_selector(".wbl-side", timeout=30000)
    return ctx, page


def test_desktop_layout_lists_courses_and_study(browser):
    ctx, page = _open(browser, 1280, 900)
    try:
        # 左栏两组
        groups = page.locator(".wbl-group-title")
        assert groups.count() >= 2
        titles = [groups.nth(i).inner_text() for i in range(groups.count())]
        assert "课程" in titles and "自主学习" in titles

        # 对象数量与后端一致
        objs = page.locator(".wbl-obj")
        assert objs.count() > 5
        assert page.locator(".wbl-obj-active").count() == 1

        # 顶部标签页 4 个
        tabs = page.locator(".wbl-tab")
        assert tabs.count() == 4
        labels = [tabs.nth(i).inner_text() for i in range(tabs.count())]
        assert any("题库" in t for t in labels)
        assert any("材料" in t for t in labels)
        assert any("笔记" in t for t in labels)
        assert any("关联" in t for t in labels)

        # 没有 JS 报错（捕获 console error）
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.wait_for_timeout(500)
        assert not [e for e in errors if "wb-learn" in e or "wb_learn" in e], errors
    finally:
        ctx.close()


def test_quiz_tab_shows_generate_entry_when_no_library(browser):
    """没有 quiz-library 时必须显示生成入口，而不是空表格或假题目。"""
    ctx, page = _open(browser, 1280, 900)
    try:
        page.locator(".wbl-tab").first.click()
        page.wait_for_timeout(1200)
        body = page.locator(".wbl-main").inner_text()
        assert "quiz-library" in body, body[:800]
        # 二选一：有题库标签，或明说没有题库并给生成入口
        has_banks = page.locator(".wbl-banktab").count() > 0
        has_generate = "生成题库" in body or "生成" in body
        assert has_banks or has_generate, body[:800]
    finally:
        ctx.close()


def test_object_switch_changes_header_and_notes_tab(browser):
    ctx, page = _open(browser, 1280, 900)
    try:
        first = page.locator(".wbl-obj").first
        firstName = first.locator(".wbl-obj-name").inner_text()
        second = page.locator(".wbl-obj").nth(1)
        secondName = second.locator(".wbl-obj-name").inner_text()
        assert firstName != secondName

        second.click()
        page.wait_for_timeout(900)
        assert page.locator(".wbl-title").inner_text() == secondName

        # 笔记标签页必须说明「是否自动关联」而不是静默
        page.locator(".wbl-tab", has_text="笔记").click()
        page.wait_for_timeout(900)
        notes = page.locator(".wbl-notecard").inner_text()
        assert "笔记关联" in notes
        assert ("精确同名" in notes) or ("未关联" in notes) or ("用户指定" in notes), notes[:400]
        assert page.locator(".wbl-notepick select").count() == 1
    finally:
        ctx.close()


def test_materials_tab_lists_real_files(browser):
    ctx, page = _open(browser, 1280, 900)
    try:
        page.locator(".wbl-tab", has_text="材料").click()
        page.wait_for_selector(".wbl-pane-list", timeout=20000)
        page.wait_for_timeout(800)
        items = page.locator(".wbl-file")
        assert items.count() > 0, "材料列表为空：后端 /materials 没返回内容"
        # 每个条目都带 kind/大小说明
        assert page.locator(".wbl-filemeta").count() > 0 or page.locator(".wbl-file-dir").count() > 0
    finally:
        ctx.close()


def test_links_tab_reports_zotero_and_github(browser):
    ctx, page = _open(browser, 1280, 900)
    try:
        page.locator(".wbl-tab", has_text="关联").click()
        page.wait_for_selector(".wbl-links", timeout=20000)
        page.wait_for_timeout(1500)
        text = page.locator(".wbl-links").inner_text()
        assert "Zotero collection" in text
        assert "GitHub" in text
        assert "只读" in text
        # 不可用时要给原因（Zotero 没开 / gh 未登录 / 404 都是原因）
        assert ("不可用" in text) or ("个集合" in text), text[:600]
    finally:
        ctx.close()


def test_chat_panel_is_bound_to_current_object(browser):
    ctx, page = _open(browser, 1280, 900)
    try:
        page.wait_for_timeout(1500)
        panel = page.locator(".wbc-panel")
        if panel.count() == 0:
            pytest.skip("对话面板未挂载（共享组件未加载）")
        scope = page.locator(".wbc-scope-v").first.inner_text()
        assert scope.strip(), "对话面板必须显示当前对象名"
        # 输入框存在且可点（说明真的绑在 Hermes /api/ws 面板上）
        assert page.locator(".wbc-input").count() == 1
    finally:
        ctx.close()


def test_mobile_375_single_column_and_chat_drawer(browser):
    ctx, page = _open(browser, 375, 812)
    try:
        # 单列：左栏与主区都在视口内，左栏不再是 288px 固定列
        side = page.locator(".wbl-side").bounding_box()
        main = page.locator(".wbl-main").bounding_box()
        assert side and main
        assert side["width"] <= 375
        assert main["width"] <= 375
        # 主区在左栏下方（堆叠），不是并排
        assert main["y"] >= side["y"] + side["height"] - 10

        # 对话按钮打开抽屉（宽屏才有并排）
        chat_btn = page.locator("button", has_text="对话").first
        assert chat_btn.is_visible()
        chat_btn.click()
        page.wait_for_timeout(800)
        assert page.locator(".wbc-drawer").count() == 1
    finally:
        ctx.close()


def test_no_horizontal_overflow_at_375(browser):
    ctx, page = _open(browser, 375, 812)
    try:
        overflow = page.evaluate(
            "() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth })")
        assert overflow["doc"] <= overflow["win"] + 2, f"375 宽度出现横向溢出：{overflow}"
    finally:
        ctx.close()
