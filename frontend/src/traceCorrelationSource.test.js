import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./api.js", import.meta.url), "utf8");

test("Mini App sends bounded request correlation and preserves server trace ids on API errors", () => {
  assert.match(source, /"X-Request-ID": createRequestId\(\)/);
  assert.match(source, /response\.headers\.get\("x-request-id"\)/);
  assert.match(source, /response\.headers\.get\("x-trace-id"\)/);
  assert.match(source, /this\.requestId = String\(correlation\.requestId \|\| ""\)/);
  assert.match(source, /this\.traceId = String\(correlation\.traceId \|\| ""\)/);
});
