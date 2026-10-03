const assert = require("node:assert/strict");
global.window = {};
require("../assets/video-controls.js");
const controls = window.ModelVideoControls;
const payload = { prompt: "waves at sunrise", negative_prompt: "", width: 832, height: 480, frames: 49, fps: 24, steps: 20, cfg: 0, seed: 0 };
assert.equal(controls.validate(payload, {}).cfg, 0);
assert.ok(controls.duration(49, 24).includes("2.04 秒"));
assert.ok(controls.duration(50, 24).includes("4n+1"));
for (const change of [{ width: 257 }, { frames: 50 }, { seed: -1 }, { seed: 1.5 }, { seed: 2 ** 53 }, { cfg: NaN }, { fps: 0 }, { steps: 1.5 }, { prompt: "" }]) {
  assert.throws(() => controls.validate({ ...payload, ...change }, {}));
}
assert.throws(() => controls.validate(payload, { lockResolution: true, width: 1280, height: 720 }));
(async () => {
  let called = null;
  const result = await controls.fetchResult(async (path, options) => {
    called = [path, options];
    return { ok: true, blob: async () => new Blob(["fixture-video"], { type: "video/mp4" }) };
  }, "fixture-job");
  assert.equal(called[0], "/v1/video/file?job_id=fixture-job");
  assert.equal(called[1].cache, "no-store");
  assert.equal(result.size, 13);
  await assert.rejects(controls.fetchResult(async () => ({ ok: false, status: 401, json: async () => ({ error: "authorization required" }) }), "fixture-job"), /authorization/);
  await assert.rejects(controls.fetchResult(async () => ({ ok: true, blob: async () => new Blob(["html"], { type: "text/html" }) }), "fixture-job"), /有效的视频/);
  await assert.rejects(controls.fetchResult(async () => {}, "../../private"), /ID 无效/);
  console.log("video parameter, duration and authorized download tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
