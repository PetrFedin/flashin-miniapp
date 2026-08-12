import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");


test("admin application mounts the permission-aware BusinessEvent workspace", () => {
  assert.match(mainSource, /import BusinessEventsPanel from "\.\/BusinessEventsPanel\.jsx"/);
  assert.match(mainSource, /<BusinessEventsPanel onUnauthorized=\{logout\} session=\{session\} \/>/);
  assert.match(panelSource, /canOrdersRead && \(/);
  assert.match(panelSource, /canReplayPermission=\{canOrdersWrite\}/);
});


test("panel reads summary, list and detail endpoints", () => {
  assert.match(panelSource, /\/api\/platform\/admin\/events\/summary/);
  assert.match(panelSource, /\/api\/platform\/admin\/events\$\{query\}/);
  assert.match(panelSource, /\/api\/platform\/admin\/events\/\$\{eventId\}/);
});


test("replay requires orders.write, confirmation and a deduplicated mutation", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "orders\.write"\)/);
  assert.match(panelSource, /!canReplayPermission/);
  assert.match(panelSource, /window\.confirm/);
  assert.match(panelSource, /business-event-replay:\$\{event\.id\}/);
  assert.match(panelSource, /method: "POST"/);
  assert.match(panelSource, /buildBusinessEventReplayBody\(reason, replacementPayload\)/);
  assert.match(panelSource, /setStatusFilter\(""\)/);
});
