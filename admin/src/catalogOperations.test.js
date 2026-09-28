import assert from "node:assert/strict";
import test from "node:test";

import {
  availableQty,
  catalogAttentionCount,
  normalizeCatalogPrice,
  normalizeCatalogStock,
  normalizeCatalogText,
} from "./catalogOperations.js";


test("catalog price validation rejects non-finite and non-positive values", () => {
  for (const value of ["", 0, -1, "invalid", Infinity, NaN]) {
    assert.ok(normalizeCatalogPrice(value).error);
  }
  assert.deepEqual(normalizeCatalogPrice("12500.129"), { value: 12500.13, error: "" });
});


test("catalog stock validation preserves reservations", () => {
  assert.deepEqual(normalizeCatalogStock("5", 2), { value: 5, error: "" });
  assert.ok(normalizeCatalogStock("1", 2).error.includes("зарезервированного"));
  for (const value of [-1, 1.5, "1.5", "invalid"]) {
    assert.ok(normalizeCatalogStock(value, 0).error);
  }
});


test("catalog text validation trims and enforces required length", () => {
  assert.deepEqual(normalizeCatalogText("  FLASHIN  ", "Бренд", 120), { value: "FLASHIN", error: "" });
  assert.ok(normalizeCatalogText("   ", "Бренд", 120).error);
  assert.ok(normalizeCatalogText("x".repeat(121), "Бренд", 120).error);
});


test("catalog availability never reports negative and attention ignores hidden products", () => {
  assert.equal(availableQty({ stock_qty: 1, reserved_qty: 2 }), 0);
  assert.equal(availableQty({ stock_qty: 5, reserved_qty: 2 }), 3);

  const products = [
    { active: true, variants: [{ stock_qty: 0, reserved_qty: 0 }] },
    { active: true, variants: [{ stock_qty: 5, reserved_qty: 1 }] },
    { active: false, variants: [{ stock_qty: 0, reserved_qty: 0 }] },
  ];
  assert.equal(catalogAttentionCount(products), 1);
});
