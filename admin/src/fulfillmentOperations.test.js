import assert from "node:assert/strict";
import test from "node:test";

import {
  fulfillmentAction,
  fulfillmentAttentionCount,
  isPicklistComplete,
  normalizeTracking,
} from "./fulfillmentOperations.js";

test("fulfillment actions expose only the next safe workflow step", () => {
  assert.deepEqual(fulfillmentAction({ status: "new" }), {
    type: "task",
    status: "picking",
    label: "Начать сборку",
  });
  assert.equal(fulfillmentAction({ status: "picking" }).type, "pick_pack");
  assert.equal(fulfillmentAction({ status: "packed" }).status, "ready");
  assert.equal(fulfillmentAction({ status: "ready" }).type, "create_shipment");
  assert.equal(
    fulfillmentAction({ status: "ready" }, { status: "created" }).type,
    "ship",
  );
  assert.equal(
    fulfillmentAction({ status: "ready" }, { status: "shipped" }).type,
    "deliver",
  );
  assert.equal(
    fulfillmentAction({ status: "ready" }, { status: "delivered" }),
    null,
  );
});

test("picklist completeness requires every ordered unit", () => {
  assert.equal(isPicklistComplete([]), false);
  assert.equal(isPicklistComplete([
    { status: "picked", picked_qty: 2, quantity: 2 },
  ]), true);
  assert.equal(isPicklistComplete([
    { status: "picked", picked_qty: 1, quantity: 2 },
  ]), false);
  assert.equal(isPicklistComplete([
    { status: "issue", picked_qty: 0, quantity: 1 },
  ]), false);
});

test("tracking is bounded and meaningful", () => {
  assert.match(normalizeTracking(" ").error, /трек-номер/i);
  assert.deepEqual(normalizeTracking("  PILOT-TRACK-1  "), {
    value: "PILOT-TRACK-1",
  });
  assert.match(normalizeTracking("x".repeat(256)).error, /255/);
});

test("attention remains until shipment is delivered", () => {
  const tasks = [
    { order_id: 1, status: "picking" },
    { order_id: 2, status: "ready" },
    { order_id: 3, status: "ready" },
  ];
  const shipments = [
    { order_id: 2, status: "shipped" },
    { order_id: 3, status: "delivered" },
  ];
  assert.equal(fulfillmentAttentionCount(tasks, shipments), 2);
});
