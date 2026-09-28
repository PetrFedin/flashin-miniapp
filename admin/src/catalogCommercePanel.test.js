import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./CatalogCommercePanel.jsx", import.meta.url), "utf8");
const workspaceSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");


test("catalog commerce workspace is mounted behind products.read", () => {
  assert.match(workspaceSource, /import CatalogCommercePanel from "\.\/CatalogCommercePanel\.jsx"/);
  assert.match(workspaceSource, /canProductsRead && <CatalogCommercePanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/);
});


test("catalog editor covers merchandising media stock external availability and sharing", () => {
  for (const token of [
    "/api/catalog/admin/products",
    "availability_status",
    "material",
    "season",
    "grid_rank",
    "sale_starts_at",
    "sale_ends_at",
    "showroom_fitting_enabled",
    "moysklad_id",
    "external_links",
    "recommendation_ids",
    "telegram_share_url",
    "uploadAdminFile",
    "Добавить видео",
    "Добавить внешний ресурс",
    "Добавить вариант",
  ]) {
    assert.match(panelSource, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});


test("inventory changes stay permission-aware and variant removal is explicit", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "inventory\.write"\)/);
  assert.match(panelSource, /disabled=\{!canWrite \|\| !canInventoryWrite\}/);
  assert.match(panelSource, /remove_variant_ids/);
  assert.match(panelSource, /Новый вариант с остатком требует inventory\.write/);
});


test("showroom operations use dedicated permissions", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "showroom\.read"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "showroom\.write"\)/);
  assert.match(panelSource, /\/api\/catalog\/admin\/showroom\/appointments/);
  assert.match(panelSource, /Подтвердить/);
  assert.match(panelSource, /Завершить/);
});
