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


test("login form supports optional TOTP without persisting the one-time code", () => {
  assert.equal(source.includes('const [totpCode, setTotpCode] = useState("")'), true);
  assert.equal(source.includes("loginAdmin(normalizedEmail, password, totpCode)"), true);
  assert.equal(source.includes('autoComplete="one-time-code"'), true);
  assert.equal(source.includes('inputMode="numeric"'), true);
  assert.equal(source.includes('setTotpCode("")'), true);
  assert.equal(source.includes("localStorage.setItem(\"totp"), false);
});


test("authenticated dashboard validates effective permissions before business data", () => {
  assert.equal(source.includes('adminJson("/api/admin/session")'), true);
  assert.equal(source.includes("normalizeAdminSession(payload)"), true);
  assert.equal(source.includes("if (!normalized.valid)"), true);
  assert.equal(source.includes("await refreshAll(nextSession)"), true);
  assert.equal(source.includes("Права администратора не подтверждены"), true);
});


test("core and operational datasets are requested only behind their read permissions", () => {
  assert.equal(source.includes('hasAdminPermission(activeSession, "products.read")'), true);
  assert.equal(source.includes('hasAdminPermission(activeSession, "orders.read")'), true);
  assert.equal(source.includes('hasAdminPermission(activeSession, "audit.read")'), true);
  assert.equal(source.includes('hasAdminPermission(activeSession, "customers.read")'), true);
});


test("sensitive mutations have explicit permission gates", () => {
  for (const permission of [
    "products.write",
    "inventory.write",
    "orders.write",
    "promo.write",
    "media.write",
    "notifications.retry",
  ]) {
    assert.equal(source.includes(`hasAdminPermission(session, "${permission}")`), true, permission);
  }
});
