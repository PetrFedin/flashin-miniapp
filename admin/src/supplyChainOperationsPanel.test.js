import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./SupplyChainOperationsPanel.jsx", import.meta.url), "utf8");
const operationsSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");


test("admin operations workspace mounts Supply Chain cockpit", () => {
  assert.match(operationsSource, /import SupplyChainOperationsPanel from "\.\/SupplyChainOperationsPanel\.jsx"/);
  assert.match(operationsSource, /<SupplyChainOperationsPanel onUnauthorized=\{onUnauthorized\} \/>/);
});


test("Supply Chain cockpit reads sanitized status and uses existing protected mutations", () => {
  assert.match(panelSource, /adminJson\("\/api\/moysklad\/operations-status"\)/);
  assert.match(panelSource, /adminJson\("\/api\/moysklad\/sync", \{ method: "POST" \}\)/);
  assert.match(panelSource, /\/api\/moysklad-deep-mapping\/sku-matches\/\$\{match\.id\}\/confirm/);
  assert.match(panelSource, /normalizeSupplyChainStatus/);
});


test("MoySklad master-data mutations require explicit operator confirmation", () => {
  const confirmationCalls = panelSource.match(/window\.confirm/g) || [];
  assert.equal(confirmationCalls.length, 2);
  assert.match(panelSource, /может изменить названия, описания, цены, категории и физические остатки/);
  assert.match(panelSource, /Подтвердить сопоставление SKU/);
});


test("Supply Chain UI deliberately avoids raw provider error rendering", () => {
  assert.doesNotMatch(panelSource, /result\.error/);
  assert.doesNotMatch(panelSource, /\.moysklad_id/);
  assert.match(panelSource, /Raw provider error скрыт/);
  assert.match(panelSource, /Требует внимания/);
});
