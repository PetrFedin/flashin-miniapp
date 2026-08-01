import assert from "node:assert/strict";
import test from "node:test";

import { createActionCoordinator } from "./actionCoordinator.js";

test("same action key shares one in-flight operation", async () => {
  let executions = 0;
  let resolveOperation;
  const coordinator = createActionCoordinator();
  const operation = () => {
    executions += 1;
    return new Promise((resolve) => { resolveOperation = resolve; });
  };

  const first = coordinator.run("checkout", operation);
  const second = coordinator.run("checkout", operation);

  assert.equal(first, second);
  assert.equal(executions, 0);
  await Promise.resolve();
  assert.equal(executions, 1);
  assert.equal(coordinator.isBusy("checkout"), true);

  resolveOperation({ id: 42 });
  assert.deepEqual(await first, { id: 42 });
  assert.equal(coordinator.isBusy("checkout"), false);
});

test("different action keys execute independently", async () => {
  const coordinator = createActionCoordinator();
  const events = [];
  const first = coordinator.run("search", async () => { events.push("search"); return 1; });
  const second = coordinator.run("profile", async () => { events.push("profile"); return 2; });

  assert.deepEqual(await Promise.all([first, second]), [1, 2]);
  assert.deepEqual(events.sort(), ["profile", "search"]);
});

test("failed action is removed and can be retried", async () => {
  const coordinator = createActionCoordinator();
  await assert.rejects(coordinator.run("support", async () => { throw new Error("offline"); }), /offline/);
  assert.equal(coordinator.isBusy("support"), false);
  assert.equal(await coordinator.run("support", async () => "ok"), "ok");
});

test("invalid coordinator inputs are rejected", () => {
  const coordinator = createActionCoordinator();
  assert.throws(() => coordinator.run("", () => null), /key/i);
  assert.throws(() => coordinator.run("x", null), /function/i);
});
