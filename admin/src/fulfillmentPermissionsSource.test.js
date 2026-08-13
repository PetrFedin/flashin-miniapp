import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./FulfillmentOperationsPanel.jsx", import.meta.url), "utf8");


test("fulfillment mutations use dedicated fulfillment.write permission", () => {
  assert.equal(source.includes('hasAdminPermission(session, "fulfillment.write")'), true);
  assert.equal(source.includes('изменение fulfillment требует fulfillment.write'), true);
  assert.equal(source.includes('нет fulfillment.write'), true);
  assert.equal(source.includes('hasAdminPermission(session, "orders.write")'), false);
});
