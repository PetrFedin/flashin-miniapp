import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PhysicalReturnPanel.jsx", import.meta.url), "utf8");


test("physical return panel is lazy and uses the authoritative admin detail endpoint", () => {
  assert.match(source, /\/api\/admin\/returns\/\$\{returnItem\.id\}\/physical/);
  assert.match(source, /Открыть физический возврат/);
  assert.match(source, /setDetail\(value\)/);
});


test("physical mutations use server idempotency headers and keep finance separate", () => {
  assert.match(source, /getPhysicalIdempotencyKey\(signature\)/);
  assert.match(source, /"Idempotency-Key": idempotencyKey/);
  assert.match(source, /clearPhysicalIdempotencyKey\(signature\)/);
  assert.match(source, /physical\/\$\{operation\}/);
  assert.doesNotMatch(source, /refund_amount/);
  assert.doesNotMatch(source, /provider_refund_id/);
});


test("operator controls follow authorize transit receive inspect disposition sequence", () => {
  assert.match(source, /mutateItem\(item, "authorize"\)/);
  assert.match(source, /physical\/in-transit/);
  assert.match(source, /mutateItem\(item, "receive"\)/);
  assert.match(source, /mutateItem\(item, "inspect"\)/);
  assert.match(source, /PHYSICAL_DISPOSITION_LABELS/);
  assert.match(source, /returns\.physical\.write/);
  assert.match(source, /resolveQuarantine/);
  assert.match(source, /physical\/quarantine\/resolve/);
  assert.match(source, /МойСклад · складское распределение/);
  assert.match(source, /Автоматический повтор неоднозначной операции заблокирован/);
});
