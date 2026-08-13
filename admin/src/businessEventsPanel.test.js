import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");
const tracePanelSource = readFileSync(new URL("./OrderOperationsTracePanel.jsx", import.meta.url), "utf8");


test("admin application mounts the permission-aware BusinessEvent workspace", () => {
  assert.match(mainSource, /import BusinessEventsPanel from "\.\/BusinessEventsPanel\.jsx"/);
  assert.match(mainSource, /<BusinessEventsPanel onUnauthorized=\{logout\} session=\{session\} \/>/);
  assert.match(panelSource, /canOrdersRead && <OrderOperationsTracePanel onUnauthorized=\{onUnauthorized\} \/>/);
  assert.match(panelSource, /canOrdersRead && <FulfillmentPanelMount onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
  assert.match(panelSource, /canService && <ServicePanelMount onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
  assert.match(panelSource, /canReplayPermission=\{canOrdersWrite\}/);
});


test("release capability mount fallbacks remain real code and fail closed without a session", () => {
  assert.match(panelSource, /return <FulfillmentOperationsPanel onUnauthorized=\{onUnauthorized\} \/>/);
  assert.match(panelSource, /return <ServiceOperationsPanel onUnauthorized=\{onUnauthorized\} \/>/);
  assert.match(panelSource, /<FulfillmentOperationsPanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
  assert.match(panelSource, /<ServiceOperationsPanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
});


test("panel reads summary, list and detail endpoints", () => {
  assert.match(panelSource, /\/api\/platform\/admin\/events\/summary/);
  assert.match(panelSource, /\/api\/platform\/admin\/events\$\{query\}/);
  assert.match(panelSource, /\/api\/platform\/admin\/events\/\$\{eventId\}/);
});


test("order incident workspace uses the read-only operations trace endpoint", () => {
  assert.match(tracePanelSource, /\/api\/ops\/orders\/\$\{numericOrderId\}\/trace/);
  assert.match(tracePanelSource, /orders\.read/);
  assert.match(tracePanelSource, /Provider payload, idempotency keys, Telegram IDs/);
  assert.doesNotMatch(tracePanelSource, /provider_payment_id/);
  assert.doesNotMatch(tracePanelSource, /external_id/);
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
