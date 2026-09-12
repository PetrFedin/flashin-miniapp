import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "BusinessEventsPanel.jsx"), "utf8");


test("BusinessEvent diagnostics require events.read instead of ordinary order access", () => {
  assert.equal(
    source.includes('const canEventsRead = hasAdminPermission(session, "events.read")'),
    true,
  );
  assert.match(source, /\{canEventsRead && \(\s*<BusinessEventsRecoveryPanel/);
  assert.doesNotMatch(source, /\{canOrdersRead && \(\s*<BusinessEventsRecoveryPanel/);
});


test("BusinessEvent replay remains a separate events.replay capability", () => {
  assert.equal(
    source.includes('const canEventsReplay = hasAdminPermission(session, "events.replay")'),
    true,
  );
  assert.equal(source.includes("canReplayPermission={canEventsReplay}"), true);
  assert.equal(source.includes("replay требует permission events.replay"), true);
  assert.equal(source.includes('const canOrdersWrite = hasAdminPermission(session, "orders.write")'), false);
  assert.equal(source.includes("canReplayPermission={canOrdersWrite}"), false);
});
