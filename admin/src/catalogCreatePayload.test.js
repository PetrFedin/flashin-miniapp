import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const source = fs.readFileSync(new URL("./CatalogCommercePanel.jsx", import.meta.url), "utf8");

test("new catalog cards omit edit-only variant deletion ids", () => {
  assert.match(
    source,
    /\.\.\.\(form\.id \? \{ remove_variant_ids: form\.remove_variant_ids \} : \{\}\)/,
    "create payload must omit remove_variant_ids while update payload keeps it",
  );
});
