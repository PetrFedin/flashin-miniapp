import assert from "node:assert/strict";
import test from "node:test";

import {
  hasAdminPermission,
  hasAnyAdminPermission,
  normalizeAdminSession,
} from "./adminPermissions.js";


test("normalized admin session exposes exact effective permissions", () => {
  const session = normalizeAdminSession({
    id: 7,
    email: " manager@flashin.test ",
    role: "manager",
    all_access: false,
    permissions: ["products.write", "products.read", "products.read"],
  });

  assert.equal(session.valid, true);
  assert.equal(session.email, "manager@flashin.test");
  assert.deepEqual(session.permissions, ["products.read", "products.write"]);
  assert.equal(hasAdminPermission(session, "products.read"), true);
  assert.equal(hasAdminPermission(session, "inventory.write"), false);
  assert.equal(hasAnyAdminPermission(session, ["inventory.write", "products.write"]), true);
});


test("owner wildcard is explicit and not inferred from role text", () => {
  const owner = normalizeAdminSession({
    id: 1,
    email: "owner@flashin.test",
    role: "owner",
    all_access: true,
    permissions: [],
  });
  const fakeOwner = normalizeAdminSession({
    id: 2,
    email: "fake@flashin.test",
    role: "owner",
    all_access: false,
    permissions: [],
  });

  assert.equal(hasAdminPermission(owner, "security.write"), true);
  assert.equal(hasAdminPermission(fakeOwner, "security.write"), false);
});


test("malformed session or permission data fails closed", () => {
  for (const payload of [
    null,
    {},
    { id: 0, email: "a@b.c", role: "manager", all_access: false, permissions: [] },
    { id: 1, email: "", role: "manager", all_access: false, permissions: [] },
    { id: 1, email: "a@b.c", role: "<script>", all_access: false, permissions: [] },
    { id: 1, email: "a@b.c", role: "manager", all_access: false, permissions: ["products.read", "<secret>"] },
  ]) {
    const session = normalizeAdminSession(payload);
    assert.equal(session.valid, false);
    assert.equal(hasAdminPermission(session, "products.read"), false);
  }
});
