import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./SupplyChainOperationsPanel.jsx", import.meta.url), "utf8");


test("manual MoySklad sync requires catalog and inventory permissions in Admin UI", () => {
  assert.equal(source.includes('hasAdminPermission(session, "products.write")'), true);
  assert.equal(source.includes('hasAdminPermission(session, "inventory.write")'), true);
  assert.equal(source.includes("const canManualSync = canCatalogMutate && canInventoryWrite"), true);
  assert.equal(source.includes("disabled={syncing || !canManualSync}"), true);
  assert.equal(source.includes("products.write + inventory.write"), true);
});


test("catalog mapping remains separately available behind products.write", () => {
  assert.equal(source.includes("if (!canCatalogMutate || match.confirmed || !match.id) return"), true);
  assert.equal(source.includes("canCatalogMutate && !match.confirmed"), true);
});
