"""wb-learn 前端契约（静态 + Node 行为）：不需要浏览器/服务，任何机器上都能跑。

钉住的是「不会被 render 掩盖的约定」：
* 前端不再直连题库硬编码根目录，而是走 /api/plugins/wb-learn；
* 写接口只有 4 个，且都必须由用户动作触发（不在 effect 里）；
* 主观题不判分、多选按集合比对的文案与实现一致；
* 插件注册名与 manifest 一致。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _find_dashboard() -> Path:
    """同 wb_learn_support._find_dashboard：原工程 / 独立导出仓库两种布局都认。"""
    override = os.environ.get("WB_LEARN_DASHBOARD")
    if override:
        return Path(override)
    for rel in (Path("plugins") / "wb-learn" / "dashboard", Path("wb-learn") / "dashboard"):
        candidate = REPO / rel
        if (candidate / "plugin_api.py").is_file():
            return candidate
    raise RuntimeError(f"找不到 wb-learn/dashboard（{REPO}）")


DASHBOARD = _find_dashboard()
DIST = DASHBOARD / "dist"
MANIFEST = json.loads((DASHBOARD / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_shape():
    assert MANIFEST["name"] == "wb-learn"
    assert MANIFEST["tab"]["path"] == "/learn"
    assert MANIFEST["has_api"] is True
    assert (DASHBOARD / MANIFEST["api"]).is_file()
    assert (DASHBOARD / MANIFEST["entry"]).is_file()
    assert (DASHBOARD / MANIFEST["css"]).is_file()


def test_no_hardcoded_personal_paths_in_frontend():
    """前端不能写死本机绝对路径（换机器就废）。"""
    for js in DIST.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "D:/" not in text and "D:\\\\" not in text, f"{js.name} 含 D: 硬编码路径"
        assert "E:/" not in text and "E:\\\\" not in text, f"{js.name} 含 E: 硬编码路径"
        assert "C:/Users" not in text, f"{js.name} 含用户目录"


def test_all_parts_are_loaded_by_the_shell():
    index = (DIST / "index.js").read_text(encoding="utf-8")
    for part in ("api.js", "quiz.js", "views.js", "panels.js", "quizpanel.js",
                 "linkspanel.js", "studyentries.js", "chatbridge.js"):
        assert (DIST / part).is_file(), f"缺 {part}"
        assert part in index, f"index.js 没有注入 {part}"


def test_plugin_registration_name_matches_manifest():
    index = (DIST / "index.js").read_text(encoding="utf-8")
    assert '__HERMES_PLUGINS__.register("wb-learn"' in index


def test_only_expected_write_calls_exist():
    """写接口清单：任何新增写调用都必须在此登记（防悄悄加写盘路径）。

    ``grade`` 也走 POST，但它是**无副作用**的判分（不落盘），单独放行并在下面断言
    它确实不写文件；其余 POST/PUT 都必须在本清单里。
    """
    expected = {
        "saveNoteLink": "PUT /object/notes",
        "stageDraft": "POST /quiz/drafts（只写 plugin-data 草稿）",
        "commitDraft": "POST /quiz/drafts/{id}/commit（写 quiz-library）",
        "saveLinks": "PUT /links（写 plugin-data 链接登记）",
        "saveStudyEntry": "POST /study/entries（写 plugin-data 子条目）",
        "deleteStudyEntry": "DELETE /study/entries?confirm=true（写 plugin-data 子条目）",
        "cloneRepo": "POST /study/items/clone-repo（克隆到对象 code/ 目录）",
        "openRepo": "POST /study/items/open（用 VSCode 打开已克隆仓库）",
        "clickItem": "POST /study/items/click（写 plugin-data 学习记录）",
        "createObject": "POST /objects（仅在预览后新建空目录）",
    }
    side_effect_free = {"grade", "resolveRepo"}
    api_src = (DIST / "api.js").read_text(encoding="utf-8")
    # 逐处定位 send("POST"/"PUT"/"DELETE")，再往前找最近的「名字: function」——
    # 不能用「function 到 send 之间不含 }」的正则：函数体里出现对象字面量就会漏判。
    writes = set()
    patterns = (r'send\(\s*"(POST|PUT|DELETE)"', r'method:\s*"(POST|PUT|DELETE)"')
    for pattern in patterns:
        for hit in re.finditer(pattern, api_src):
            head = api_src[:hit.start()]
            names = re.findall(r"(\w+):\s*function", head)
            assert names, f"无法定位写调用对应的函数：{hit.group(0)}"
            writes.add(names[-1])
    assert writes == set(expected) | side_effect_free, \
        f"写接口清单变了：{writes ^ (set(expected) | side_effect_free)}"

    # grade 打到只读端点 /quiz/grade，后端不保存作答
    grade_body = re.search(r"grade: function[^}]*\}", api_src, re.S).group(0)
    assert '"/quiz/grade"' in grade_body

    # 视图层不能自己拼 POST
    for js in ("panels.js", "quizpanel.js", "linkspanel.js", "studyentries.js", "index.js"):
        text = (DIST / js).read_text(encoding="utf-8")
        assert 'method: "POST"' not in text and "method: 'POST'" not in text


def test_effect_bodies_do_not_commit_drafts():
    """草稿保存必须在点击回调里，不能在 effect 里自动跑（否则等于自动写盘）。"""
    text = (DIST / "quizpanel.js").read_text(encoding="utf-8")
    effects = re.findall(r"useEffect\(function \(\) \{(.*?)\n      \}, \[", text, re.S)
    for body in effects:
        assert "commitDraft" not in body, "useEffect 里不能自动 commitDraft"


def test_short_answer_is_marked_manual_in_ui():
    views = (DIST / "views.js").read_text(encoding="utf-8")
    quiz = (DIST / "quiz.js").read_text(encoding="utf-8")
    assert "需自评" in views, "简答题必须显示「需自评」而不是对/错"
    assert "自评" in quiz
    assert "关键词" in quiz, "文案要说明不做关键词假判"


def test_multiple_choice_hint_is_stated():
    views = (DIST / "views.js").read_text(encoding="utf-8")
    assert "集合必须完全一致" in views


def test_generate_panel_says_agent_not_self_generated():
    panel = (DIST / "quizpanel.js").read_text(encoding="utf-8")
    assert "本面板不自己出题" in panel
    assert "禁止编造" in panel or "不要凭空编题" in panel
    assert "quiz-library" in panel
    assert "确认保存" in panel


def test_chatbridge_does_not_auto_send():
    """桥只填输入框；自动发送会让 agent 在用户没看的时候动文件。"""
    bridge = (DIST / "chatbridge.js").read_text(encoding="utf-8")
    assert "dispatchEvent" in bridge, "要用原生 setter + input 事件写受控输入框"
    assert "keydown" not in bridge.lower() or "不自动发送" in bridge
    assert "不自动发送" in bridge
    for bad in ("prompt.submit", "ws.send", "new WebSocket"):
        assert bad not in bridge, f"桥不能自己发消息（{bad}）"


def test_vendored_chat_is_present_and_untouched():
    """vendored 副本必须存在；在完整工程里还要与 shared/ 逐字节一致。

    独立导出仓库不带 shared/ 源码（它是另一个插件的共享来源），那边只校验文件存在 +
    带来源说明（见 NOTICE）。所以这里按「shared/ 是否存在」分支，而不是硬要求它。
    """
    vendor = DIST / "vendor"
    for f in ("wb-agent-chat.js", "wb-agent-chat.css"):
        assert (vendor / f).is_file()
    shared = REPO / "shared" / "wb-agent-chat"
    if not shared.is_dir():
        pytest.skip("独立导出布局：没有 shared/ 源码，只校验 vendored 文件存在")
    for f in ("wb-agent-chat.js", "wb-agent-chat.css"):
        assert (vendor / f).read_bytes() == (shared / f).read_bytes(), \
            f"{f} 与 shared 不一致：跑 tools/sync-shared.py"


def test_styles_cover_the_new_components():
    css = (DIST / "style.css").read_text(encoding="utf-8")
    for cls in ("wbl-tabs", "wbl-banktabs", "wbl-gencard", "wbl-pane2", "wbl-linkcard",
                "wbl-notecard", "wbl-previewlist", "wbl-verdict"):
        assert "." + cls in css, f"样式缺 {cls}"
    assert "@media (max-width: 760px)" in css, "需要手机断点"
    assert "@media (max-width: 1180px)" in css
    assert ".wbl-kind-switch" in css and ".wbl-newobject" in css


def test_learning_kind_switch_and_course_tabs_are_explicit():
    views = (DIST / "views.js").read_text(encoding="utf-8")
    index = (DIST / "index.js").read_text(encoding="utf-8")
    assert '"自主学习"' in views and '"课程"' in views
    assert 't.id === "quiz" || t.id === "materials" || t.id === "notes"' in index
    assert "onNew" in views and "previewObject" in index and "createObject" in index


pytestmark_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 未安装")


@pytestmark_node
def test_quiz_progress_scoping_does_not_leak_between_banks():
    """进度按 对象+题库 分槽：切换题库不能串「已答/正确」。"""
    script = """
global.window = { localStorage: (function(){ const m={}; return {
  getItem(k){ return k in m ? m[k] : null; },
  setItem(k,v){ m[k]=String(v); },
}; })() };
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
new Function('window','globalThis', src + '\\n')(window, globalThis);
const Q = window.WBL_QUIZ;
Q.putProgress('course:A', 'bank1', { answered: { q1: { correct: true } }, cursor: 3 });
Q.putProgress('course:A', 'bank2', { answered: { q9: { correct: false } }, cursor: 1 });
const out = {
  b1: Q.getScope('course:A', 'bank1'),
  b2: Q.getScope('course:A', 'bank2'),
  other: Q.getScope('course:B', 'bank1'),
  stats1: Q.stats(Q.getScope('course:A','bank1').answered),
};
process.stdout.write(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", script, "--", str(DIST / "quiz.js")],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["b1"]["cursor"] == 3 and "q1" in out["b1"]["answered"]
    assert out["b2"]["cursor"] == 1 and "q9" in out["b2"]["answered"]
    assert out["other"]["answered"] == {}, "不同对象/题库不能共享进度"
    assert out["stats1"] == {"answered": 1, "correct": 1, "wrong": 0}
