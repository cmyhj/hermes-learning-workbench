/*
 * 面板传输层的重连/幂等测试：用一个受控的假 WebSocket 驱动真实的 AgentClient，
 * 验证的是客户端自己的行为（这正是「断线后不重复提交」的责任所在）。
 *
 *     node --test tests/test_wb_chat_client.mjs
 *
 * 覆盖：
 *   1. 断开时在途请求以 CONNECTION_LOST 失败 —— 调用方据此提示「结果未知」
 *   2. AgentClient 不会自己重发任何请求（重连后只发新的一次 connect 调用）
 *   3. respond() 镜像服务端请求帧（approval / clarify 走同一条 socket）
 *   4. 未连接时 request 立即拒绝，不会静默挂住
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CODE = readFileSync(path.join(HERE, "..", "shared", "wb-agent-chat", "wb-agent-chat.js"), "utf8");

function loadApi() {
  const win = { localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} } };
  new Function("window", "document", CODE)(win, undefined);
  return win.__WB_CHAT__;
}

const api = loadApi();

/** 假 WebSocket：记录发出的帧，能把服务端帧推给客户端，能模拟断开。 */
class FakeSocket {
  constructor() {
    this.readyState = 0; // CONNECTING
    this.sent = [];
    this.onopen = null;
    this.onerror = null;
    this.onclose = null;
    this.onmessage = null;
  }
  open() {
    this.readyState = 1;
    this.onopen && this.onopen();
  }
  send(text) {
    if (this.readyState !== 1) throw new Error("socket not open");
    this.sent.push(JSON.parse(text));
  }
  push(frame) {
    this.onmessage && this.onmessage({ data: JSON.stringify(frame) });
  }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose && this.onclose({ code: 1006 });
  }
  // 模拟对端断开（不是用户主动 close）
  drop() {
    this.readyState = 3;
    this.onclose && this.onclose({ code: 1006 });
  }
  framesOf(method) {
    return this.sent.filter((f) => f.method === method);
  }
}

function makeClient() {
  const sockets = [];
  const client = new api.AgentClient({
    WebSocket: function () {
      const s = new FakeSocket();
      sockets.push(s);
      // 下一个 tick 打开，让测试有机会先挂监听
      setTimeout(() => s.open(), 0);
      return s;
    },
    buildUrl: () => Promise.resolve("ws://test/api/ws?token=fake"),
  });
  return { client, sockets };
}

const tick = () => new Promise((r) => setTimeout(r, 5));

test("在途请求在断开时以 CONNECTION_LOST 失败（不是挂住或静默成功）", async () => {
  const { client, sockets } = makeClient();
  await client.connect();
  const ws = sockets[0];
  const pending = client.request("session.create", { cwd: "D:/x" });
  assert.equal(ws.framesOf("session.create").length, 1);

  ws.drop(); // 对端断开
  await assert.rejects(pending, /CONNECTION_LOST/);
  assert.equal(client.state, "error", "断线后面板必须显示断开，而不是假装已连接");
});

test("断线不会让客户端自己重发上一轮提交", async () => {
  const { client, sockets } = makeClient();
  await client.connect();
  const ws = sockets[0];
  const p1 = client.request("prompt.submit", { session_id: "s1", text: "写文件" });
  const rid = ws.sent[ws.sent.length - 1].id;
  ws.push({ jsonrpc: "2.0", id: rid, result: { status: "streaming" } });
  await p1;
  assert.equal(ws.framesOf("prompt.submit").length, 1);

  ws.drop();
  await tick();
  client.close();
  // 重新连接（面板做的那一步）
  await client.connect();
  const ws2 = sockets[1];
  assert.ok(ws2, "应当新建一条 socket");
  assert.equal(ws2.framesOf("prompt.submit").length, 0,
    "重连时客户端绝不能自动重发 prompt.submit —— 服务端可能已经在跑");
  client.close();
});

test("重连只发新的连接请求，不重放旧请求", async () => {
  const { client, sockets } = makeClient();
  await client.connect();
  const ws = sockets[0];
  const done = client.request("session.create", {});
  ws.push({ jsonrpc: "2.0", id: ws.sent[0].id, result: { session_id: "abc" } });
  assert.deepEqual(await done, { session_id: "abc" });

  ws.drop();
  await client.connect();
  const ws2 = sockets[1];
  // 重连后是「全新连接」：还没有任何应用层帧
  assert.equal(ws2.sent.length, 0);

  const resumed = client.request("session.resume", { session_id: "20260101_000000_abcdef" });
  assert.equal(ws2.framesOf("session.resume").length, 1);
  ws2.push({ jsonrpc: "2.0", id: ws2.sent[0].id, result: { session_id: "new", session_key: "20260101_000000_abcdef" } });
  assert.equal((await resumed).session_key, "20260101_000000_abcdef");
  assert.equal(ws.framesOf("session.resume").length, 0, "旧 socket 上不该补发 resume");
});

test("服务端请求可以用同一个 socket 应答（approval / clarify）", async () => {
  const { client, sockets } = makeClient();
  await client.connect();
  const ws = sockets[0];
  let got = null;
  client.opts.onRequest = (f) => { got = f; };
  ws.push({ jsonrpc: "2.0", id: "srq-abc123", method: "approval", params: { session_id: "s1", command: "rm x" } });
  assert.ok(got, "服务端请求应交给 onRequest");
  assert.equal(got.id, "srq-abc123");

  assert.equal(client.respond(got, { choice: "deny" }), true);
  const reply = ws.sent[ws.sent.length - 1];
  assert.equal(reply.id, "srq-abc123");
  assert.equal(reply.result.choice, "deny");
  assert.equal(reply.method, undefined, "应答帧不应带 method（服务端按「无 method 的响应帧」识别）");
});

test("未连接时 request 立即拒绝而不是挂住", async () => {
  const { client } = makeClient();
  await assert.rejects(client.request("session.create", {}), /未连接/);
});

test("事件帧交给 onEvent，且不干扰在途 RPC 的应答匹配", async () => {
  const { client, sockets } = makeClient();
  const events = [];
  client.opts.onEvent = (p) => events.push(p);
  await client.connect();
  const ws = sockets[0];
  const p = client.request("prompt.submit", { session_id: "s1", text: "hi" });
  const rid = ws.sent[0].id;
  ws.push({ jsonrpc: "2.0", method: "event", params: { type: "message.delta", session_id: "s1", payload: { text: "你" }, seq: 1 } });
  ws.push({ jsonrpc: "2.0", id: rid, result: { status: "streaming" } });
  assert.deepEqual(await p, { status: "streaming" });
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "message.delta");
});

test("请求超时后不应把迟到的应答当成成功", async () => {
  const { client, sockets } = makeClient();
  await client.connect();
  const ws = sockets[0];
  const p = client.request("session.create", {}, 10); // 10ms
  const rid = ws.sent[0].id;
  await assert.rejects(p, /超时/);
  // 迟到应答：已被丢弃，不能抛未处理异常
  ws.push({ jsonrpc: "2.0", id: rid, result: { session_id: "late" } });
  await tick();
  assert.equal(client.pending.size, 0);
});
