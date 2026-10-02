const assert = require("assert");
global.window = {};
require("../assets/model-capabilities.js");

const states = window.MODEL_CAPABILITIES;
assert.strictEqual(Object.keys(states).length, 28);
assert.strictEqual(states.qwen_image_edit_2511.label, "待检查编辑环境");
assert.strictEqual(states.qwen_image_2_1_int8.label, "可直接使用");
assert.strictEqual(states.flux2_klein_4b_fp8.label, "可直接使用");
assert.strictEqual(states.qwen3_14b_q6.label, "可直接使用");
assert.strictEqual(states.qwen3_vl_8b_q6.label, "需要适配器");
assert.strictEqual(states.flux_1_dev.label, "Drive 文件不完整");
assert.strictEqual(states.wan22_ti2v_5b.label, "可直接使用");
