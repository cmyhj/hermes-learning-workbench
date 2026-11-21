/*
 * wb-learn 前端 · API 客户端
 *
 * 只做一件事：把后端接口包成带缓存语义的小函数。视图层不直接拼 URL，
 * 这样「哪些请求可写、哪些只读」在这里一眼可见。
 *
 * 写接口（全部需要用户显式动作）：
 *   saveNoteLink / stageDraft / commitDraft / saveLinks
 * 只读接口：
 *   loadObjects / loadMaterials / previewMaterial / listBanks / loadBank / grade
 *
 * 刻意**不**做「自动重试写请求」：写失败要让人看见，不能后台偷偷重放。
 */
(function (root) {
  "use strict";

  var BASE = "/api/plugins/wb-learn";

  function qs(params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === "") return;
      parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  function messageOf(err) {
    if (!err) return "未知错误";
    var msg = err.message || String(err);
    // fetchJSON 会抛 status + 后端 detail；尽量把人类的说明挑出来
    if (err.detail) {
      if (typeof err.detail === "string") return err.detail;
      if (err.detail.message) return err.detail.message +
        (err.detail.issues && err.detail.issues.length ? "：" + err.detail.issues.slice(0, 5).join("；") : "");
    }
    return msg;
  }

  function make(fetchJSON) {
    function get(path, params) {
      return fetchJSON(BASE + path + qs(params));
    }

    function send(method, path, body) {
      return fetchJSON(BASE + path, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
    }

    return {
      base: BASE,
      messageOf: messageOf,

      // ---- 概览 / 对象
      health: function () { return get("/health"); },
      objects: function () { return get("/objects"); },
      object: function (id) { return get("/object", { id: id }); },
      saveNoteLink: function (id, vault) {
        return send("PUT", "/object/notes" + qs({ id: id, vault: vault || "" }), {});
      },

      // ---- 资料（只读）
      materials: function (id, subdir) { return get("/materials", { id: id, subdir: subdir || "" }); },
      previewMaterial: function (id, path) { return get("/materials/preview", { id: id, path: path }); },
      noteFiles: function (id) { return get("/notes/files", { id: id }); },
      previewNote: function (id, path) { return get("/notes/preview", { id: id, path: path }); },

      // ---- 题库
      banks: function (id) { return get("/quiz/banks", { id: id }); },
      bank: function (id, bank, type, limit) {
        return get("/quiz/bank", { id: id, bank: bank, type: type || "", limit: limit || 300 });
      },
      grade: function (question, chosen) {
        return send("POST", "/quiz/grade", { question: question, chosen: chosen });
      },
      stageDraft: function (objectId, title, bank) {
        return send("POST", "/quiz/drafts", { object_id: objectId, title: title, bank: bank });
      },
      draft: function (draftId) { return get("/quiz/drafts/" + encodeURIComponent(draftId)); },
      commitDraft: function (objectId, draftId, filename, overwrite) {
        return send("POST", "/quiz/drafts/" + encodeURIComponent(draftId) + "/commit",
          { object_id: objectId, filename: filename, overwrite: !!overwrite });
      },

      // ---- 只读外链
      links: function (id) { return get("/links", { id: id }); },
      saveLinks: function (id, patch) {
        var body = { id: id };
        if (Object.prototype.hasOwnProperty.call(patch, "zotero")) body.zotero = patch.zotero;
        if (Object.prototype.hasOwnProperty.call(patch, "github")) body.github = patch.github;
        return send("PUT", "/links", body);
      },
      zoteroCollections: function () { return get("/zotero/collections"); },
      zoteroCollection: function (key) { return get("/zotero/collection", { key: key }); },
      githubRepo: function (repo) { return get("/github/repo", { repo: repo }); },
    };
  }

  root.WBL_API = { make: make, messageOf: messageOf, BASE: BASE };
})(typeof window !== "undefined" ? window : globalThis);
