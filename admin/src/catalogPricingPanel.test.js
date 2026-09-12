import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./CatalogPricingPanel.jsx", import.meta.url), "utf8");
const supportSource = readFileSync(new URL("./CatalogSupportOperationsPanel.jsx", import.meta.url), "utf8");


test("scheduled pricing panel is permission-gated and mounted with catalog operations", () => {
  assert.match(panelSource, /products\.read/);
  assert.match(panelSource, /products\.write/);
  assert.match(supportSource, /CatalogPricingPanel/);
});


test("scheduled pricing panel uses dedicated Admin pricing endpoints", () => {
  assert.match(panelSource, /\/api\/catalog\/admin\/pricing/);
  assert.match(panelSource, /\/api\/catalog\/admin\/products\/\$\{row\.product_id\}\/pricing/);
  assert.match(panelSource, /method: "PATCH"/);
});


test("operator can set or explicitly clear promo price and UTC sale boundaries", () => {
  for (const token of ["promo_price", "sale_starts_at", "sale_ends_at", "UTC", "configured_promo_price"] ) {
    assert.match(panelSource, new RegExp(token));
  }
  assert.match(panelSource, /promoPrice = promoText \? Number\(promoText\) : null/);
  assert.match(panelSource, /if \(!value\) return null/);
  assert.match(panelSource, /sale_starts_at: utcIso/);
  assert.match(panelSource, /sale_ends_at: utcIso/);
});


test("operator UI surfaces blocked pricing configuration instead of hiding it", () => {
  assert.match(panelSource, /configuration_error/);
  assert.match(panelSource, /BLOCKED/);
  assert.match(panelSource, /PROMO ACTIVE/);
});
