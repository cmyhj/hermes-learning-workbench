/*
 * wb-learn 前端 · 与共享对话面板的桥（不改 shared/wb-agent-chat）
 *
 * 共享组件只接受 props（moduleId/objectId/getContext），没有「程序化发消息」的接口，
 * 而 shared/ 是多个插件共用的只读来源，不能为了本插件加接口。所以这里做两件很薄的事：
 *
 *   insertPrompt(text)  —— 用原生 value setter + input 事件把文字写进对话面板的输入框
 *                          （绕开 React 的受控 value tracker），**不自动发送**：
 *                          发送仍是用户的动作，界面上会提示「已填入，按 Enter 发送」。
 *   lastAgentReply()    —— 读对话面板里最后一条 assistant 气泡的文本，供「提取 JSON」用。
 *
 * 都不伪造数据：读不到就返回 null / false，由调用方如实报错。
 */
(function (root) {
  "use strict";

  var INPUT_SELECTORS = [".wbc-input", "textarea.wbc-input", ".wbc-compose textarea"];
  var BUBBLE_SELECTOR = ".wbc-msg-agent .wbc-bubble";

  function findComposer(doc) {
    doc = doc || root.document;
    for (var i = 0; i < INPUT_SELECTORS.length; i++) {
      var el = doc.querySelector(INPUT_SELECTORS[i]);
      if (el) return el;
    }
    return null;
  }

  /** React 受控组件：直接改 value 会被 tracker 吞掉，必须走原型 setter + input 事件。 */
  function setNativeValue(el, value) {
    var proto = el.tagName === "TEXTAREA" ? root.HTMLTextAreaElement.prototype
                                          : root.HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new root.Event("input", { bubbles: true }));
  }

  function insertPrompt(text, doc) {
    var el = findComposer(doc);
    if (!el || !text) return false;
    setNativeValue(el, text);
    try { el.focus(); } catch (e) { /* 焦点失败不影响写入 */ }
    return true;
  }

  function lastAgentReply(doc) {
    doc = doc || root.document;
    var nodes = doc.querySelectorAll(BUBBLE_SELECTOR);
    if (!nodes.length) return null;
    var text = (nodes[nodes.length - 1].textContent || "").trim();
    return text || null;
  }

  /** 对话面板是否已经挂载（决定「生成入口」的按钮是否可用，而不是假装成功）。 */
  function chatReady(doc) {
    doc = doc || root.document;
    return !!(findComposer(doc) || doc.querySelector(".wbc-panel"));
  }

  root.WBL_CHATBRIDGE = {
    insertPrompt: insertPrompt,
    lastAgentReply: lastAgentReply,
    chatReady: chatReady,
  };
})(typeof window !== "undefined" ? window : globalThis);
