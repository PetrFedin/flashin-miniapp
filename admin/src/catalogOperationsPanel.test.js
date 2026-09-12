import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./CatalogOperationsPanel.jsx", import.meta.url), "utf8");
const operationsSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");


test("admin operations workspace mounts the catalog panel only with products.read", () => {
  assert.match(operationsSource, /import CatalogOperationsPanel from "\.\/CatalogOperationsPanel\.jsx"/);
  assert.match(operationsSource, /canProductsRead && <CatalogOperationsPanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
});


test("catalog panel owns the full master-data and inventory paths", () => {
  assert.match(panelSource, /adminJson\("\/api\/admin\/products"\)/);
  assert.match(panelSource, /\/api\/admin\/products\/\$\{product\.id\}/);
  assert.match(panelSource, /\/api\/admin\/products\/\$\{product\.id\}\/active/);
  assert.match(panelSource, /\/api\/admin\/variants\/\$\{variant\.id\}\/stock/);
  assert.match(panelSource, /normalizeCatalogPrice/);
  assert.match(panelSource, /normalizeCatalogStock/);
});


test("catalog mutations are split between product and inventory permissions", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "products\.write"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "inventory\.write"\)/);
  assert.match(panelSource, /Недостаточно прав: изменение карточки требует products\.write/);
  assert.match(panelSource, /Недостаточно прав: изменение остатка требует inventory\.write/);
});


test("destructive catalog changes require explicit operator confirmation", () => {
  assert.match(panelSource, /Скрыть товар \$\{product\.sku\}/);
  assert.match(panelSource, /Изменить физический остаток \$\{variant\.sku\}/);
  const confirmationCalls = panelSource.match(/window\.confirm/g) || [];
  assert.equal(confirmationCalls.length, 2);
});


test("catalog panel exposes stable accessibility labels for browser acceptance", () => {
  assert.match(panelSource, /Каталог и остатки/);
  assert.match(panelSource, /Сохранить товар \{product\.sku\}/);
  assert.match(panelSource, /Остаток \$\{variant\.sku\}/);
  assert.match(panelSource, /Обновить остаток \{variant\.sku\}/);
});
