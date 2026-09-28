import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./main.jsx", import.meta.url), "utf8");


test("catalog creation defaults to zero stock and never fabricates inventory permission", () => {
  assert.equal(source.includes('variants: [{ size: "M", sku: "", stock_qty: 0, color: "" }]'), true);
  assert.equal(source.includes('stock_qty: canInventoryWrite ? Number(variant.stock_qty) : 0'), true);
  assert.equal(source.includes('disabled={!canInventoryWrite}'), true);
  assert.equal(source.includes('Начальный остаток выше нуля требует inventory.write'), true);
});


test("CSV import requires both catalog and inventory write permissions", () => {
  assert.equal(source.includes('if (!canProductsWrite || !canInventoryWrite)'), true);
  assert.equal(source.includes('canProductsWrite && canInventoryWrite && ('), true);
  assert.equal(source.includes('products.write + inventory.write'), true);
});
