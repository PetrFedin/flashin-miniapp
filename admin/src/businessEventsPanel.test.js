import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");
const tracePanelSource = readFileSync(new URL("./OrderOperationsTracePanel.jsx", import.meta.url), "utf8");


test("admin application mounts permission-aware operational workspaces", () => {
  assert.match(mainSource, /import BusinessEventsPanel from "\.\/BusinessEventsPanel\.jsx"/);
  assert.match(mainSource, /<BusinessEventsPanel onUnauthorized=\{logout\} session=\{session\} \/>/);
  assert.match(panelSource, /canOrdersRead && <OrderOperationsTracePanel onUnauthorized=\{onUnauthorized\} \/>/);
  assert.match(panelSource, /canOrdersRead && <FulfillmentPanelMount onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
  assert.match(panelSource, /canService && <ServicePanelMount onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
  assert.match(panelSource, /hasAdminPermission\(session, "events\.read"\)/);
  assert.match(panelSource, /\{canEventsRead && \(\s*<BusinessEventsRecoveryPanel/);
  assert.doesNotMatch(panelSource, /\{canOrdersRead && \(\s*<BusinessEventsRecoveryPanel/);
  assert.match(panelSource, /canReplayPermission=\{canEventsReplay\}/);
});


test("release capability mount fallbacks remain real code and fail closed without a session", () => {
  assert.match(panelSource, /return <FulfillmentOperationsPanel onUnauthorized=\{onUnauthorized\} \/>/);
  assert.match(panelSource, /return <ServiceOperationsPanel onUnauthorized=\{onUnauthorized\} \/>/);
  assert.match(panelSource, /<FulfillmentOperationsPanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
  assert.match(panelSource, /<ServiceOperationsPanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
});


test("event diagnostics read summary, list and detail endpoints only from their gated child panel", () => {
  assert.match(panelSource, /\/api\/platform\/admin\/events\/summary/);
  assert.match(panelSource, /\/api\/platform\/admin\/events\$\{query\}/);
  assert.match(panelSource, /\/api\/platform\/admin\/events\/\$\{eventId\}/);
  assert.match(panelSource, /function BusinessEventsRecoveryPanel/);
  assert.match(panelSource, /\{canEventsRead && \(\s*<BusinessEventsRecoveryPanel/);
});


test("order incident workspace exposes safe inventory attention without raw ledger source", () => {
  assert.match(tracePanelSource, /\/api\/ops\/orders\/\$\{numericOrderId\}\/trace/);
  assert.match(tracePanelSource, /orders\.read/);
  assert.match(tracePanelSource, /Inventory ledger/);
  assert.match(tracePanelSource, /inventoryMovements/);
  assert.match(tracePanelSource, /inventoryInvalidRows/);
  assert.match(tracePanelSource, /inventory source/);
  assert.doesNotMatch(tracePanelSource, /provider_payment_id/);
  assert.doesNotMatch(tracePanelSource, /external_id/);
  assert.doesNotMatch(tracePanelSource, /\.source/);
});


test("replay requires events.replay, confirmation and a deduplicated mutation", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "events\.replay"\)/);
  assert.doesNotMatch(panelSource, /hasAdminPermission\(session, "orders\.write"\)/);
  assert.match(panelSource, /!canReplayPermission/);
  assert.match(panelSource, /window\.confirm/);
  assert.match(panelSource, /business-event-replay:\$\{event\.id\}/);
  assert.match(panelSource, /method: "POST"/);
  assert.match(panelSource, /buildBusinessEventReplayBody\(reason, replacementPayload\)/);
  assert.match(panelSource, /setStatusFilter\(""\)/);
});
