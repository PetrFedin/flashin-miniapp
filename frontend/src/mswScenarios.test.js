import assert from "node:assert/strict";
import test from "node:test";

import { handlersForScenario, scenarioHandlers } from "./mocks/handlers.js";

const REQUIRED = [
  "stock-race",
  "payment-fail",
  "payment-pending",
  "refund-pending",
  "delivery-unavailable",
  "provider-timeout",
  "reservation-expired",
];

test("MSW exposes every deterministic master-plan failure scenario", () => {
  assert.deepEqual(Object.keys(scenarioHandlers).sort(), [...REQUIRED].sort());
  for (const name of REQUIRED) {
    assert.ok(handlersForScenario(name).length > 0, name);
  }
});

test("unknown MSW scenario fails safe with no interception", () => {
  assert.deepEqual(handlersForScenario("unknown"), []);
});
