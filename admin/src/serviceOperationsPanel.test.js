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


test("Service Operations only loads datasets the role can read", () => {
  assert.match(panelSource, /if \(canSupport\) entries\.push\(\["support", "\/api\/support\/admin\/tickets"\]\)/);
  assert.match(panelSource, /if \(canPrivacyRead\) entries\.push\(\["privacy", "\/api\/privacy\/admin\/requests"\]\)/);
  assert.match(panelSource, /if \(canReturnsRead\) entries\.push\(\["returns", "\/api\/admin\/returns"\]\)/);
});


test("privacy and refund mutations fail closed without write permissions", () => {
  assert.match(panelSource, /if \(!canPrivacyWrite\)/);
  assert.match(panelSource, /privacy\.write/);
  assert.match(panelSource, /if \(!canReturnsWrite\)/);
  assert.match(panelSource, /orders\.write/);
  assert.match(panelSource, /canPrivacyWrite && \(/);
  assert.match(panelSource, /canReturnsWrite && \(/);
});
