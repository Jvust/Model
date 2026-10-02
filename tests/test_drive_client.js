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
  console.log("drive-client nested lookup tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
