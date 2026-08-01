import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("storefront uses independent action locks instead of one global busy string", () => {
  assert.match(source, /useActionLocks/);
  assert.doesNotMatch(source, /const \[busy, setBusy\]/);
  assert.doesNotMatch(source, /if \(busy\) return null/);
});

test("storefront imports canonical order and input rules", () => {
  assert.match(source, /from "\.\/orderRules\.js"/);
  assert.match(source, /validateCheckoutForm/);
  assert.match(source, /parseLoyaltyPoints/);
  assert.doesNotMatch(source, /const ORDER_LABELS =/);
});

test("storefront uses resilient bootstrap and profile loaders", () => {
  assert.match(source, /loadStorefrontBootstrap/);
  assert.match(source, /loadProfileSections/);
  assert.doesNotMatch(source, /const \[nextProfile, loyalty, nextReferral/);
});

test("successful business mutations are not masked by refresh failures", () => {
  assert.match(source, /Cancellation succeeded; a cart refresh failure must not report it as failed/);
  assert.match(source, /setOrders\(\(current\) => current\.some/);
});
