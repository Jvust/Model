const assert = require("node:assert/strict");
global.window = { MODEL_CONFIG: {} };
require("../assets/drive-client.js");

let query;
let files = [{ id: "fixture-folder", name: "Qwen-Image-Edit-2511" }];
global.fetch = async (url, options) => {
  query = new URL(url).searchParams.get("q");
  assert.equal(options.headers.Authorization, "Bearer fixture-token");
  return { ok: true, json: async () => ({ files }) };
};

(async () => {
  const client = window.DriveModelClient;
  const result = await client.findFolderByName("fixture-token", "Qwen-Image-Edit-2511", null);
  assert.equal(result.folder.id, "fixture-folder");
  assert.ok(query.includes("name = 'Qwen-Image-Edit-2511'"));
  assert.ok(!query.includes("in parents"), "nested linked folders must not be root-only");
  await client.findFolderByName("fixture-token", "AI-Model-Vault");
  assert.ok(query.includes("'root' in parents"));
  await client.findFolderByName("fixture-token", "test's folder", "fixture-parent");
  assert.ok(query.includes("'fixture-parent' in parents"));
  assert.ok(query.includes("test\\'s folder"));
  files = [];
  assert.equal((await client.findFolderByName("fixture-token", "missing", null)).folder, null);
  files = [{ id: "one" }, { id: "two" }];
  await assert.rejects(client.findFolderByName("fixture-token", "duplicate", null), /多个同名/);
  await assert.rejects(client.findFolderByName("", "folder", null), /尚未授权/);
  await assert.rejects(client.findFolderByName("fixture-token", "", null), /不能为空/);
  const folder = "application/vnd.google-apps.folder";
  window.DriveModelIndex = { isModelFile: f => f.name.endsWith(".safetensors"), isPackageSupportFile: f => f.name.endsWith(".json") };
  let calls = [];
  const root = { id: "root-fixture", name: "Qwen-Image-Edit-2511", mimeType: folder, resourceKey: "fixture-key" };
  global.fetch = async (url, options) => {
    calls.push(url);
    if (!new URL(url).searchParams.has("q")) return { ok: true, json: async () => root };
    assert.equal(options.headers["X-Goog-Drive-Resource-Keys"], "root-fixture/fixture-key");
    return { ok: true, json: async () => ({ files: [
      { id: "cache-fixture", name: "offload", mimeType: folder },
      { id: "root-fixture", name: "cycle", mimeType: folder },
      { id: "weights-fixture", name: "model.safetensors" },
      { id: "weights-fixture", name: "model.safetensors" },
      { id: "config-fixture", name: "config.json" }
    ] }) };
  };
  const scan = await client.scanModelTree("fixture-token", root.id);
  assert.equal(scan.scannedFolders, 1);
  assert.equal(scan.modelFiles, 1);
  assert.equal(scan.supportFiles, 1);
  assert.equal(calls.length, 2, "cache directories and repeated folders must not be traversed");
  global.fetch = async url => ({ ok: true, json: async () => new URL(url).searchParams.has("q") ? { files: [], nextPageToken: "repeated" } : root });
  await assert.rejects(client.scanModelTree("fixture-token", root.id), /重复分页/);
  console.log("drive-client nested lookup tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
