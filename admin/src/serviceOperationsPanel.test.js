import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./ServiceOperationsPanel.jsx", import.meta.url), "utf8");


test("Service Operations derives every section from effective permissions", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "support\.write"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "privacy\.read"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "privacy\.write"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "orders\.read"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "orders\.write"\)/);
});


test("Service Operations keeps release capability endpoints explicit and only loads permitted datasets", () => {
  assert.match(panelSource, /support: "\/api\/support\/admin\/tickets"/);
  assert.match(panelSource, /privacy: "\/api\/privacy\/admin\/requests"/);
  assert.match(panelSource, /returns: "\/api\/admin\/returns"/);
  assert.match(panelSource, /if \(canSupport\) entries\.push\(\["support", SERVICE_ENDPOINTS\.support\]\)/);
  assert.match(panelSource, /if \(canPrivacyRead\) entries\.push\(\["privacy", SERVICE_ENDPOINTS\.privacy\]\)/);
  assert.match(panelSource, /if \(canReturnsRead\) entries\.push\(\["returns", SERVICE_ENDPOINTS\.returns\]\)/);
});


test("privacy and refund mutations fail closed without write permissions", () => {
  assert.match(panelSource, /if \(!canPrivacyWrite\)/);
  assert.match(panelSource, /privacy\.write/);
  assert.match(panelSource, /if \(!canReturnsWrite\)/);
  assert.match(panelSource, /orders\.write/);
  assert.match(panelSource, /canPrivacyWrite && \(/);
  assert.match(panelSource, /canReturnsWrite && \(/);
});
