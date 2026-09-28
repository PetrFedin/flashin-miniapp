import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./CatalogIntentOperationsPanel.jsx", import.meta.url), "utf8");
const mountSource = readFileSync(new URL("./CatalogSupportOperationsPanel.jsx", import.meta.url), "utf8");


test("catalog intent queue is permission-gated and uses dedicated admin endpoints", () => {
  assert.match(source, /products\.read/);
  assert.match(source, /products\.write/);
  assert.match(source, /\/api\/catalog\/admin\/intents/);
  assert.match(source, /method: "PATCH"/);
});


test("catalog intent queue keeps customer PII out of the operator table", () => {
  assert.match(source, /PII скрыты/);
  assert.match(source, /Customer #\{item\.customer_id\}/);
  assert.doesNotMatch(source, /customer_email/);
  assert.doesNotMatch(source, /customer_phone/);
  assert.doesNotMatch(source, /telegram_id/);
});


test("catalog intent queue can explicitly clear quote and ETA", () => {
  assert.match(source, /quote_amount: draft\.quote_amount === "" \? null/);
  assert.match(source, /estimated_ready_at: draft\.estimated_ready_at/);
  assert.match(source, /: null,/);
});


test("catalog support surface mounts the intent queue for product operators", () => {
  assert.match(mountSource, /CatalogIntentOperationsPanel/);
  assert.match(mountSource, /canProductsRead && \(/);
});
