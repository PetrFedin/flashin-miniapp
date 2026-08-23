import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "BusinessEventsPanel.jsx"), "utf8");


test("BusinessEvent replay UI is gated by events.replay instead of orders.write", () => {
  assert.equal(
    source.includes('const canEventsReplay = hasAdminPermission(session, "events.replay")'),
    true,
  );
  assert.equal(source.includes("canReplayPermission={canEventsReplay}"), true);
  assert.equal(source.includes("replay требует permission events.replay"), true);
  assert.equal(source.includes('const canOrdersWrite = hasAdminPermission(session, "orders.write")'), false);
  assert.equal(source.includes("canReplayPermission={canOrdersWrite}"), false);
});
