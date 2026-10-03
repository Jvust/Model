const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const asset = name => fs.readFileSync(path.join(__dirname, "../assets", name), "utf8");
const models = [
  { id: "qwen_image_edit_2511", name: "Qwen Image Edit 2511", workspace: "image-edit", backend: "Diffusers", packagePath: "image_edit/Qwen/model" },
  { id: "wan", name: "Wan2.2-TI2V-5B", workspace: "video-generation", backend: "ComfyUI", vaultMissing: true },
  { id: "qwen3", name: "Qwen3", workspace: "chat", backend: "llama.cpp" },
  { id: "unregistered", name: "Unknown", workspace: "unknown-new-type", backend: "Custom" }
];

function harness() {
  let focus = null;
  const nodes = new Map(), timers = new Map();
  let timerId = 0;
  class Element {
    constructor() { this._value = ""; this.children = []; this.attributes = {}; this.events = {}; this.style = {}; this.dataset = {}; this.classList = { add() {} }; this.isConnected = true; this.disabled = false; this.hidden = false; this.checked = false; }
    set value(value) { this._value = String(value); }
    get value() { return this._value; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text || ""; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); return child; }
    addEventListener(name, fn) { this.events[name] = fn; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    removeAttribute(name) { delete this.attributes[name]; }
    scrollIntoView() {}
    focus() { focus = this; }
    pause() {}
    load() {}
  }
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }, createElement() { return new Element(); }, get activeElement() { return focus; } };
  const window = { addEventListener() {}, MODEL_CONFIG: {}, DriveModelIndex: { extensionOf: () => "" }, matchMedia: () => ({ matches: true }) };
  const context = vm.createContext({ window, document, console, Headers, AbortController,
    URL: { createObjectURL: () => "blob:fixture", revokeObjectURL() {} },
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    fetch: (...args) => context.fetchImpl(...args), fetchImpl: () => { throw Error("Unexpected fetch"); },
    localStorage: { getItem: () => null, setItem() {} }, sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} }
  });
  vm.runInContext(asset("library-controls.js"), context);
  vm.runInContext(asset("video-controls.js"), context);
  const app = asset("app.js").replace("  init().catch(showError);", `window.__test = {
    renderModels, resetModelFilters, openVideoWorkspace, closeVideoWorkspace, generateVideo, stopVideo, pollVideoStatus,
    setModels(value) { models = value; }, setRuntime(value) { runtimeState = value; runtimeBase = "http://127.0.0.1:8765"; },
    state() { return { videoCommandBusy, selectedVideoModel, videoPollEpoch, videoViewEpoch }; }
  };`);
  vm.runInContext(app, context);
  document.getElementById("modelType").value = "all";
  return { context, window, document, timers, api: window.__test, node: document.getElementById };
}
const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; };
const response = (data, ok = true) => ({ ok, status: ok ? 200 : 409, json: async () => data });
const idle = () => response({ phase: "idle", running: false });
function readyVideo(h) {
  h.api.openVideoWorkspace(models[1]);
  h.api.setRuntime({ runtime_version: 17 });
  h.node("videoPrompt").value = "waves at sunrise";
  h.node("videoNegative").value = "";
}
(async () => {
  const h = harness(), filter = h.window.ModelLibraryControls.filter;
  assert.equal(filter(models).length, 4);
  assert.equal(filter(models, "ＱＷＥＮ   ２５１１")[0].id, "qwen_image_edit_2511");
  assert.equal(filter(models, "DIFFUSERS model", "image-edit").length, 1);
  assert.equal(filter(models, "视频").length, 1);
  assert.equal(filter(models, "", "video-generation", true).length, 0);
  assert.equal(filter(models, "", "generic")[0].id, "unregistered");
  assert.equal(filter([null, ...models], "unknown").length, 1);
  assert.equal(filter(null).length, 0);
  assert.equal(filter(models, "<script>").length, 0);
  assert.equal(models.length, 4, "filter must not mutate original index");
  h.api.setModels(models);
  h.node("modelSearch").value = "unmatched";
  h.api.renderModels();
  assert.equal(h.node("modelCount").textContent, "显示 0 / 4 个模型包");
  assert.match(h.node("modelList").children[0].children[0].textContent, /没有符合/);
  h.node("modelSearch").value = "2511";
  h.api.renderModels();
  assert.equal(h.node("modelList").children.length, 1, "bootstrap card must not leak into filtered results");
  assert.equal(h.node("modelCount").textContent, "显示 1 / 4 个模型包");
  h.api.setModels([]); h.api.renderModels();
  assert.match(h.node("modelList").children[0].textContent, /还没有模型索引/);
  h.api.resetModelFilters();
  assert.equal(h.node("modelSearch").value, "");
  assert.equal(h.node("resetModelFilters").disabled, true);

  // Actual app.js handlers with delayed transport: only one mutation can be submitted.
  const submit = harness(); readyVideo(submit);
  const gate = deferred(); let posts = 0;
  submit.context.fetchImpl = () => { posts++; return gate.promise; };
  const one = submit.api.generateVideo();
  await submit.api.generateVideo();
  await submit.api.stopVideo();
  assert.equal(posts, 1);
  submit.api.closeVideoWorkspace();
  gate.resolve(response({ error: "late backend rejection" }, false));
  await one;
  assert.equal(submit.node("error").textContent, "", "closed workspace must ignore late rejection");
  assert.equal(submit.node("videoWorkspace").hidden, true);
  assert.equal(submit.api.state().videoCommandBusy, false);
  assert.equal(submit.timers.size, 0);

  // Lost POST response is not proof of failure: reconcile status before enabling retries.
  const uncertain = harness(); readyVideo(uncertain); let gets = 0;
  uncertain.context.fetchImpl = async url => {
    if (url.endsWith("/generate")) throw Error("connection lost");
    gets++; return response({ phase: "generating", running: true, adapter: "wan2.2-ti2v-5b" });
  };
  await uncertain.api.generateVideo();
  assert.equal(gets, 1);
  assert.equal(uncertain.node("videoGenerateBtn").disabled, true);
  assert.equal(uncertain.node("videoStopBtn").disabled, false);

  // Concurrent manual refreshes share the current view's in-flight status request.
  const polling = harness(); readyVideo(polling);
  const statusGate = deferred(); let polls = 0;
  polling.context.fetchImpl = () => { polls++; return statusGate.promise; };
  const firstPoll = polling.api.pollVideoStatus();
  await polling.api.pollVideoStatus();
  assert.equal(polls, 1);
  polling.api.closeVideoWorkspace();
  statusGate.resolve(response({ phase: "failed", running: false, error: "stale failure" }));
  await firstPoll;
  assert.equal(polling.node("error").textContent, "");
  assert.equal(polling.timers.size, 0);

  // A newer view wins even when an older status response arrives last.
  const switching = harness(); readyVideo(switching);
  const old = deferred(); let count = 0;
  switching.context.fetchImpl = () => (++count === 1 ? old.promise : Promise.resolve(idle()));
  const oldPoll = switching.api.pollVideoStatus();
  switching.api.openVideoWorkspace({ name: "HunyuanVideo-1.5", id: "hunyuan" });
  await new Promise(resolve => setImmediate(resolve));
  old.resolve(response({ phase: "failed", running: false, error: "previous view" }));
  await oldPoll;
  assert.equal(switching.node("videoStatus").textContent, "等待任务");
  assert.equal(switching.node("error").textContent, "");
  assert.equal(switching.node("videoGenerateBtn").disabled, false);

  // Only completed, validated status unlocks result handling; duplicate polls do not refetch media.
  const completed = harness(); readyVideo(completed); let downloads = 0;
  completed.context.fetchImpl = async () => response({ phase: "complete", running: false, job_id: "fixture-job", adapter: "wan2.2-ti2v-5b", output_name: "../../video.mp4" });
  completed.window.ModelVideoControls.fetchResult = async () => { downloads++; return { size: 10, type: "video/mp4" }; };
  await completed.api.pollVideoStatus(); await completed.api.pollVideoStatus();
  assert.equal(downloads, 1);
  assert.equal(completed.node("videoDownload").download, ".._.._video.mp4");
  const malformed = harness(); readyVideo(malformed);
  malformed.context.fetchImpl = async () => response({});
  await malformed.api.pollVideoStatus();
  assert.equal(malformed.node("videoGenerateBtn").disabled, true);
  assert.match(malformed.node("error").textContent, /不完整的视频状态/);

  const stopping = harness(); readyVideo(stopping); stopping.node("videoStopBtn").disabled = false;
  const stopGate = deferred(); let stops = 0;
  stopping.context.fetchImpl = () => { stops++; return stopGate.promise; };
  const stop = stopping.api.stopVideo(); await stopping.api.stopVideo();
  assert.equal(stops, 1);
  stopping.api.closeVideoWorkspace(); stopGate.resolve(response({ video: { phase: "cancelled", running: false } })); await stop;
  assert.equal(stopping.node("videoWorkspace").hidden, true);
  assert.equal(stopping.timers.size, 0);
  console.log("library filtering, empty states, video mutation/poll races, recovery and result reuse tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
