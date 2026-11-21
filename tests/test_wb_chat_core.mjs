/*
 * 共享聊天组件（shared/wb-agent-chat）的纯逻辑测试 —— 真实在 Node 里执行组件源码，
 * 不 mock 组件本身：只在全局造一个最小 window（localStorage / WebSocket 缺失也无所谓，
 * 这两个只在对应用例里注入）。
 *
 *     node --test tests/test_wb_chat_core.mjs
 *
 * 覆盖：
 *   1. 事件解析：真实 JSON-RPC `method:"event"` 帧形状（params.type / payload / seq）
 *   2. 会话隔离：别的 session_id 的帧不允许进入当前对象的状态
 *   3. 错误与停止：message.complete(status=error) → error 状态；未完成的工具标红
 *   4. 重连幂等：同一 seq 的重放帧不重复追加文本
 *   5. 会话槽位与持久化：模块+对象 → 各自的 stored id，互不覆盖
 *   6. 上下文文本：明确标注「数据而非指令」
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, "..", "shared", "wb-agent-chat", "wb-agent-chat.js");
const CODE = readFileSync(SRC, "utf8");

/** 在一个最小 window 里执行组件源码，拿到它的导出 API。 */
function loadApi(extra = {}) {
  const store = new Map();
  const win = {
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    ...extra,
  };
  // 组件用 IIFE 挂在 window 上；这里给它一个假的 window 再取回来。
  const fn = new Function("window", "document", CODE);
  fn(win, undefined);
  assert.ok(win.__WB_CHAT__, "组件应在 window.__WB_CHAT__ 上导出 API");
  return win.__WB_CHAT__;
}

/** 与服务端真实发帧形状一致：{"jsonrpc":"2.0","method":"event","params":{type,session_id,payload,seq}} */
function frame(api, state, type, payload, seq, sessionId) {
  return api.applyEvent(state, {
    type,
    session_id: sessionId || state.sessionId,
    payload,
    seq,
  });
}

const api = loadApi();

function fresh(sessionId = "runtime-1") {
  return { sessionId, messages: [], status: "idle", seq: undefined, notices: [] };
}

// ---------------------------------------------------------------- 1. 事件解析
test("流式 delta 累积成一条 assistant 气泡，seq 递增", () => {
  let s = fresh();
  s = frame(api, s, "message.start", {}, 1);
  assert.equal(s.status, "streaming");
  s = frame(api, s, "message.delta", { text: "你" }, 2);
  s = frame(api, s, "message.delta", { text: "好" }, 3);
  s = frame(api, s, "message.complete", { text: "你好", status: "complete" }, 4);
  assert.equal(s.messages.length, 1);
  assert.equal(s.messages[0].role, "assistant");
  assert.equal(s.messages[0].text, "你好");
  assert.equal(s.messages[0].pending, false);
  assert.equal(s.status, "idle");
});

test("delta 的 text:null 是段边界，不能变成气泡内容", () => {
  let s = fresh();
  s = frame(api, s, "message.start", {}, 1);
  s = frame(api, s, "message.delta", { text: "前半" }, 2);
  s = frame(api, s, "message.delta", { text: null }, 3);
  s = frame(api, s, "message.delta", { text: "后半" }, 4);
  assert.equal(s.messages[0].text, "前半后半");
});

test("工具状态：tool.start → running，tool.complete → done 并合并同一行", () => {
  let s = fresh();
  s = frame(api, s, "message.start", {}, 1);
  s = frame(api, s, "tool.start", { tool_id: "t1", name: "terminal", context: "ls" }, 2);
  s = frame(api, s, "tool.complete", { tool_id: "t1", name: "terminal", summary: "12 行" }, 3);
  const tools = s.messages[0].tools;
  assert.equal(tools.length, 1, "同一个 tool_id 必须合并，不能出现两行");
  assert.equal(tools[0].state, "done");
  assert.equal(tools[0].summary, "12 行");
});

test("没有 assistant 气泡时收到 delta（重连只补到尾巴）会自动补一条", () => {
  let s = fresh();
  s = frame(api, s, "message.delta", { text: "半句话" }, 5);
  assert.equal(s.messages.length, 1);
  assert.equal(s.messages[0].text, "半句话");
});

// ---------------------------------------------------------------- 2. 会话隔离
test("别的会话的帧被丢弃（切对象不能串输出）", () => {
  let s = fresh("session-A");
  s = frame(api, s, "message.start", {}, 1, "session-A");
  s = frame(api, s, "message.delta", { text: "A 的内容" }, 2, "session-A");
  const before = JSON.stringify(s);
  // B 会话的帧喂进 A 的状态
  const after = api.applyEvent(s, {
    type: "message.delta", session_id: "session-B", payload: { text: "B 的内容" }, seq: 3,
  });
  assert.equal(after, s, "外来帧必须原样返回同一个对象（不产生新状态）");
  assert.equal(JSON.stringify(after), before);
});

test("两个对象的槽位互不覆盖", () => {
  const a = api.slotKey("wb-project", "2026-10_毕设");
  const b = api.slotKey("wb-project", "2026-10_形势与政策");
  const m = api.slotKey("wb-project", null);
  assert.notEqual(a, b);
  assert.notEqual(a, m);
  assert.equal(m, "wb-project::__module__");

  api.setStoredSession("wb-project", "2026-10_毕设", "20261121_101010_aaaaaa");
  api.setStoredSession("wb-project", null, "20261121_111111_bbbbbb");
  assert.equal(api.getStoredSession("wb-project", "2026-10_毕设"), "20261121_101010_aaaaaa");
  assert.equal(api.getStoredSession("wb-project", null), "20261121_111111_bbbbbb");
  assert.equal(api.getStoredSession("wb-project", "未选过的项目"), null);

  // 清掉一个不影响另一个
  api.setStoredSession("wb-project", null, null);
  assert.equal(api.getStoredSession("wb-project", null), null);
  assert.equal(api.getStoredSession("wb-project", "2026-10_毕设"), "20261121_101010_aaaaaa");

  // 不同模块的同名对象也是独立槽位
  api.setStoredSession("wb-learn", "2026-10_毕设", "20261121_121212_cccccc");
  assert.equal(api.getStoredSession("wb-learn", "2026-10_毕设"), "20261121_121212_cccccc");
});

// ---------------------------------------------------------------- 3. 错误与停止
test("message.complete(status=error) → error 状态 + 遗留工具标红", () => {
  let s = fresh();
  s = frame(api, s, "message.start", {}, 1);
  s = frame(api, s, "tool.start", { tool_id: "t1", name: "write_file" }, 2);
  s = frame(api, s, "message.complete", { status: "error", error: "provider 502" }, 3);
  assert.equal(s.status, "error");
  assert.equal(s.messages[0].error, "provider 502");
  assert.equal(s.messages[0].tools[0].state, "error", "未完成的工具不能显示为成功");
});

test("用户中断（interrupted）不算错误，恢复 idle", () => {
  let s = fresh();
  s = frame(api, s, "message.start", {}, 1);
  s = frame(api, s, "message.delta", { text: "写到一半" }, 2);
  s = frame(api, s, "message.complete", { text: "", status: "interrupted" }, 3);
  assert.equal(s.status, "idle");
  assert.equal(s.messages[0].error, null);
  assert.equal(s.messages[0].pending, false);
});

test("会话级 error 事件进入 notices 并标记 error", () => {
  let s = fresh();
  s = frame(api, s, "error", { message: "agent 初始化失败" }, 1);
  assert.equal(s.status, "error");
  assert.match(s.notices[0].text, /agent 初始化失败/);
});

// ---------------------------------------------------------------- 4. 重连幂等
test("重放同一批帧（同 seq）不重复追加", () => {
  let s = fresh();
  s = frame(api, s, "message.start", {}, 1);
  s = frame(api, s, "message.delta", { text: "完整回复" }, 2);
  s = frame(api, s, "message.complete", { text: "完整回复", status: "complete" }, 3);
  const once = s.messages[0].text;

  // 断线重连：session.events.since 把 1..3 原样重放一遍
  for (const [type, payload, seq] of [
    ["message.start", {}, 1],
    ["message.delta", { text: "完整回复" }, 2],
    ["message.complete", { text: "完整回复", status: "complete" }, 3],
  ]) {
    s = frame(api, s, type, payload, seq);
  }
  assert.equal(s.messages.length, 1, "重放不能长出新气泡");
  assert.equal(s.messages[0].text, once, "重放不能把文本追加两遍");
});

test("乱序/回退的 seq 被忽略，seq 只增不减", () => {
  let s = fresh();
  s = frame(api, s, "message.delta", { text: "2" }, 2);
  s = frame(api, s, "message.delta", { text: "1" }, 1); // 旧帧
  assert.equal(s.seq, 2);
  assert.equal(s.messages[0].text, "2");
});

// ---------------------------------------------------------------- 5. 上下文
test("上下文文本明确标注「数据而非指令」，含模块/对象/真实路径/cwd", () => {
  const text = api.buildContextText({
    module: "项目区（wb-project）",
    object: "2026-10_毕设",
    cwd: "D:/ActiveProjects/2026-10_毕设",
    files: [{ path: "D:/ActiveProjects/2026-10_毕设/2026-10_毕设.pm", role: "本项目 .pm" }],
    facts: ["任务总数 12"],
    notes: ["写 .pm 前先备份"],
  });
  assert.match(text, /数据而非指令/);
  assert.match(text, /2026-10_毕设/);
  assert.match(text, /D:\/ActiveProjects\/2026-10_毕设\/2026-10_毕设\.pm/);
  assert.match(text, /不是系统指令/);
  assert.match(text, /必须先向用户确认/);
});

test("模块级（没有对象）时上下文写明「模块级」而不是编一个对象名", () => {
  const text = api.buildContextText({ module: "学习区", object: null, cwd: "D:/x" });
  assert.match(text, /模块级/);
});
