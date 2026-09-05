import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./ProductIntentExperience.jsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("./catalogApi.js", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");


test("Mini App exposes preorder and made-to-order request flow without payment", () => {
  assert.match(source, /Предзаказ \/ под заказ/);
  assert.match(source, /Отправить заявку без оплаты/);
  assert.match(source, /не запускает оплату/);
  assert.match(source, /normal_checkout_available/);
  assert.doesNotMatch(source, /payment[_ -]?url/i);
});


test("catalog client uses dedicated intent routes", () => {
  assert.match(apiSource, /\/api\/catalog\/intents\/eligible-products/);
  assert.match(apiSource, /\/api\/catalog\/intents\/me/);
  assert.match(apiSource, /catalogRequest\("\/api\/catalog\/intents"/);
});


test("product intent experience is mounted in Mini App root", () => {
  assert.match(mainSource, /ProductIntentExperience/);
  assert.match(mainSource, /catalog-intents\.css/);
});
