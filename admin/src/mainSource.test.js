import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./main.jsx", import.meta.url), "utf8");


test("admin UI does not offer forbidden generic order statuses", () => {
  assert.equal(source.includes('["created","payment_created","paid"'), false);
  assert.equal(source.includes("<select value={o.status}"), false);
  assert.equal(source.includes("orderAction(order)"), true);
});


test("admin CSV export uses authenticated download flow", () => {
  assert.equal(source.includes("downloadAdminFile"), true);
  assert.equal(source.includes('href={`${API}/api/admin/orders/export-csv`}'), false);
});


test("unused hidden operations are not loaded by the dashboard", () => {
  for (const path of [
    "/api/campaigns",
    "/api/crm/profiles",
    "/api/moysklad/sync-logs",
    "/api/reconciliation/stock",
    "/api/fulfillment/tasks",
    "/api/webhook-destinations",
  ]) {
    assert.equal(source.includes(path), false, `${path} should not be loaded without a visible section`);
  }
});


test("login form does not expose a preset production email", () => {
  assert.equal(source.includes('useState("admin@flashin.store")'), false);
});
