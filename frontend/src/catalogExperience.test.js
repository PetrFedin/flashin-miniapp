import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mainSource = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");
const experienceSource = readFileSync(new URL("./CatalogExperience.jsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("./catalogApi.js", import.meta.url), "utf8");


test("Catalog+ is mounted without replacing the existing checkout application", () => {
  assert.match(mainSource, /<App \/>/);
  assert.match(mainSource, /<CatalogExperience \/>/);
  assert.match(mainSource, /catalog-plus\.css/);
});


test("Catalog+ exposes the requested merchandising filters and sorting", () => {
  for (const token of [
    "brand",
    "category",
    "material",
    "season",
    "availability_status",
    "badge",
    "size",
    "color",
    "min_price",
    "max_price",
    "price_asc",
    "price_desc",
    "rating_desc",
  ]) {
    assert.match(experienceSource, new RegExp(token));
  }
});


test("client product detail keeps cart restricted to real local availability", () => {
  assert.match(experienceSource, /selectedVariant\?\.available_qty > 0/);
  assert.match(experienceSource, /addToCart\(selected\.id, selectedVariant\.id, 1\)/);
  assert.match(experienceSource, /external_availability/);
  assert.doesNotMatch(experienceSource, /addToCart\([^\n]*external/);
});


test("client detail covers wishlist canonical Telegram share feedback showroom videos and recommendations", () => {
  for (const token of [
    "addWishlist",
    "removeWishlist",
    "getProductShare",
    "mini_app_deep_link",
    "telegram_share_url",
    "submitProductFeedback",
    "createShowroomAppointment",
    "listMyShowroomAppointments",
    "<video",
    "Complete the look",
  ]) {
    assert.match(experienceSource, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(experienceSource, /setSelected\(\{ \.\.\.detail, share \}\)/);
});


test("catalog API uses the authenticated FLASHIN session and canonical product share endpoint", () => {
  assert.match(apiSource, /flashin_token/);
  assert.match(apiSource, /Authorization/);
  assert.match(apiSource, /\/api\/catalog\/products/);
  assert.match(apiSource, /\/api\/catalog\/products\/\$\{Number\(productId\)\}\/share/);
  assert.match(apiSource, /\/api\/catalog\/showroom\/appointments/);
});
