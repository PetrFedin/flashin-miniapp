import assert from "node:assert/strict";
import test from "node:test";

import {
  buildBusinessEventReplayBody,
  canReplayBusinessEvent,
  compactEventError,
  eventStatusLabel,
  formatEventDate,
} from "./businessEvents.js";


test("replay is available only for terminal failed events", () => {
  assert.equal(canReplayBusinessEvent({ status: "failed" }), true);
  assert.equal(canReplayBusinessEvent({ status: "pending" }), false);
  assert.equal(canReplayBusinessEvent({ status: "processed" }), false);
  assert.equal(canReplayBusinessEvent(null), false);
});


test("replay body trims reason and omits an empty payload override", () => {
  assert.deepEqual(
    buildBusinessEventReplayBody("  Исправлен destination  ", "   "),
    { reason: "Исправлен destination" },
  );
});


test("replay body accepts only a JSON object payload", () => {
  assert.deepEqual(
    buildBusinessEventReplayBody("Исправлены данные", '{"order_id":123,"paid":true}'),
    {
      reason: "Исправлены данные",
      payload: { order_id: 123, paid: true },
    },
  );
  assert.throws(
    () => buildBusinessEventReplayBody("Исправлены данные", "not-json"),
    /валидным JSON/,
  );
  assert.throws(
    () => buildBusinessEventReplayBody("Исправлены данные", "[]"),
    /JSON-объектом/,
  );
  assert.throws(
    () => buildBusinessEventReplayBody("нет", ""),
    /минимум 5 символов/,
  );
});


test("event display helpers are bounded and safe", () => {
  assert.equal(eventStatusLabel("failed"), "Требует вмешательства");
  assert.equal(eventStatusLabel("custom"), "custom");
  assert.equal(formatEventDate("invalid"), "—");
  assert.equal(compactEventError(""), "Ошибка не зафиксирована");
  assert.equal(compactEventError("abcdefgh", 5), "abcd…");
});
