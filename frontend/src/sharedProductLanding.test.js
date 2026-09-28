import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./SharedProductLanding.jsx", import.meta.url), "utf8");


test("shared product accepts direct web and Telegram Main Mini App parameters", () => {
  assert.match(source, /searchParams\.delete\("product"\)/);
  assert.match(source, /tgWebAppStartParam/);
  assert.match(source, /initDataUnsafe\?\.start_param/);
  assert.match(source, /\^product_\(\[1-9\]\[0-9\]\*\)\$/);
});


test("closing shared landing removes URL product start parameter before reload", () => {
  assert.match(source, /url\.searchParams\.delete\("product"\)/);
  assert.match(source, /url\.searchParams\.delete\("tgWebAppStartParam"\)/);
  assert.match(source, /window\.history\.replaceState/);
});
