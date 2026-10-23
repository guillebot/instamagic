import assert from "node:assert/strict";
import { defaultEdgeMode } from "./blurEdgeMode.ts";

assert.equal(defaultEdgeMode("rectangle"), "hard");
assert.equal(defaultEdgeMode("ellipse"), "soft");
console.log("blurEdgeMode.test.mjs ok");
