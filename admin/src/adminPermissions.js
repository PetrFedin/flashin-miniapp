const PERMISSION_PATTERN = /^[a-z][a-z0-9_.:-]{0,99}$/;
const ROLE_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;

export function normalizeAdminSession(payload) {
  const invalid = {
    valid: false,
    id: 0,
    email: "",
    role: "unknown",
    allAccess: false,
    permissions: [],
  };
  if (!payload || typeof payload !== "object") return invalid;

  const id = Number(payload.id);
  const email = String(payload.email ?? "").trim().slice(0, 255);
  const role = String(payload.role ?? "").trim().toLowerCase();
  const allAccess = payload.all_access === true;
  const rawPermissions = Array.isArray(payload.permissions) ? payload.permissions : null;
  if (!Number.isInteger(id) || id <= 0 || !email || !ROLE_PATTERN.test(role) || !rawPermissions) {
    return invalid;
  }

  const permissions = [];
  for (const raw of rawPermissions) {
    const permission = String(raw ?? "").trim();
    if (!PERMISSION_PATTERN.test(permission)) return invalid;
    if (!permissions.includes(permission)) permissions.push(permission);
  }
  permissions.sort();

  return {
    valid: true,
    id,
    email,
    role,
    allAccess,
    permissions,
  };
}

export function hasAdminPermission(session, permission) {
  if (!session?.valid || !PERMISSION_PATTERN.test(String(permission || ""))) return false;
  return session.allAccess === true || session.permissions.includes(permission);
}

export function hasAnyAdminPermission(session, permissions) {
  return (Array.isArray(permissions) ? permissions : []).some((permission) => (
    hasAdminPermission(session, permission)
  ));
}
