import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./CatalogSupportOperationsPanel.jsx", import.meta.url), "utf8");
const workspaceSource = readFileSync(new URL("./BusinessEventsPanel.jsx", import.meta.url), "utf8");


test("showroom queue is mounted independently from products.read", () => {
  assert.match(workspaceSource, /canShowroomRead = hasAdminPermission\(session, "showroom\.read"\)/);
  assert.match(
    workspaceSource,
    /\(canProductsRead \|\| canShowroomRead\) && <CatalogSupportOperationsPanel onUnauthorized=\{onUnauthorized\} session=\{session\} \/>/,
  );
});


test("support can operate showroom without receiving product editing capabilities", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "showroom\.read"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "showroom\.write"\)/);
  assert.match(panelSource, /\/api\/catalog\/admin\/showroom\/appointments/);
  assert.match(panelSource, /Подтвердить визит/);
  assert.match(panelSource, /Завершить визит/);
  assert.doesNotMatch(panelSource, /\/api\/catalog\/admin\/products/);
  assert.doesNotMatch(panelSource, /inventory\.write/);
});


test("feedback moderation remains product-permission controlled", () => {
  assert.match(panelSource, /hasAdminPermission\(session, "products\.read"\)/);
  assert.match(panelSource, /hasAdminPermission\(session, "products\.write"\)/);
  assert.match(panelSource, /\/api\/catalog\/admin\/feedback\?status=/);
  assert.match(panelSource, /\/api\/catalog\/admin\/feedback\/\$\{item\.id\}/);
  assert.match(panelSource, /Скрыть отзыв/);
  assert.match(panelSource, /Опубликовать отзыв/);
});
