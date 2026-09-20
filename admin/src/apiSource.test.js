import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./api.js", import.meta.url), "utf8");


test("admin login transports optional TOTP only when supplied", () => {
  assert.equal(source.includes('loginAdmin(email, password, totpCode = "")'), true);
  assert.equal(source.includes('const normalizedTotp = String(totpCode || "").trim()'), true);
  assert.equal(source.includes("if (normalizedTotp) payload.totp_code = normalizedTotp"), true);
  assert.equal(source.includes('body: JSON.stringify(payload)'), true);
});


test("admin TOTP is never written to persistent browser storage by API layer", () => {
  assert.equal(source.includes('localStorage.setItem("totp'), false);
  assert.equal(source.includes('sessionStorage.setItem("totp'), false);
});


test("explicit Admin token clearing revokes the exact starting server session best effort", () => {
  assert.equal(source.includes("function revokeAdminSessionBestEffort(token)"), true);
  assert.equal(source.includes('fetch(`${API_BASE}/api/admin/logout`'), true);
  assert.equal(source.includes('method: "POST"'), true);
  assert.equal(source.includes('headers: { Authorization: `Bearer ${token}` }'), true);
  assert.equal(source.includes('cache: "no-store"'), true);
  assert.equal(source.includes("keepalive: true"), true);
  assert.equal(source.includes("const tokenAtLogout = getAdminToken()"), true);
  assert.equal(source.includes("clearAdminTokenLocal();\n  revokeAdminSessionBestEffort(tokenAtLogout);"), true);
});


test("401 cleanup is local-only and cannot revoke a replacement Admin session", () => {
  assert.equal(source.includes("function clearAdminTokenIfCurrent(tokenAtStart)"), true);
  assert.equal(
    source.includes("if (getAdminToken() !== tokenAtStart) throw staleAdminSessionError()"),
    true,
  );
  const clearBlock = source
    .split("function clearAdminTokenIfCurrent(tokenAtStart)", 2)[1]
    .split("async function errorDetail", 1)[0];
  assert.equal(clearBlock.includes("clearAdminTokenLocal()"), true);
  assert.equal(clearBlock.includes("revokeAdminSessionBestEffort"), false);
  assert.equal(
    source.includes("if (response.status === 401 && auth) clearAdminTokenIfCurrent(tokenAtStart)"),
    true,
  );
});


test("admin mutation dedupe and successful responses are bound to one auth session", () => {
  assert.equal(source.includes("const tokenAtStart = auth ? getAdminToken() : \"\""), true);
  assert.equal(source.includes("const coordinationScope = auth ? tokenAtStart : PUBLIC_REQUEST_SCOPE"), true);
  assert.equal(
    source.includes("headers: authHeaders(auth, headers, tokenAtStart)"),
    true,
  );
  assert.equal(source.includes("}, coordinationScope);"), true);
  assert.equal(source.match(/assertAdminSessionUnchanged\(auth, tokenAtStart\)/g)?.length >= 2, true);
});


test("stale unauthorized responses cannot clear a newer admin token", () => {
  assert.equal(source.includes("function clearAdminTokenIfCurrent(tokenAtStart)"), true);
  assert.equal(
    source.includes("if (getAdminToken() !== tokenAtStart) throw staleAdminSessionError()"),
    true,
  );
  assert.equal(
    source.includes("if (response.status === 401 && auth) clearAdminTokenIfCurrent(tokenAtStart)"),
    true,
  );
});


test("admin downloads reject a response if the admin session changes mid-flight", () => {
  assert.equal(source.includes("const tokenAtStart = getAdminToken()"), true);
  assert.equal(source.includes("headers: authHeaders(true, {}, tokenAtStart)"), true);
  assert.equal(source.includes("if (response.status === 401) clearAdminTokenIfCurrent(tokenAtStart)"), true);
  assert.equal(source.match(/assertAdminSessionUnchanged\(true, tokenAtStart\)/g)?.length >= 2, true);
});


test("media upload preserves one idempotency key across ambiguous retries", () => {
  assert.equal(source.includes('const MEDIA_UPLOAD_KEY_PREFIX = "flashin_media_upload_key:"'), true);
  assert.equal(source.includes('if (path !== "/api/media/upload")'), true);
  assert.equal(source.includes('headers: { "Idempotency-Key": uploadKey }'), true);
  assert.equal(source.includes("setStoredUploadKey(storageName, uploadKey)"), true);
  assert.equal(source.includes("clearStoredUploadKey(storageName)"), true);
  const uploadBlock = source
    .split('export async function uploadAdminFile(path, file, field = "file")', 2)[1]
    .split("export async function downloadAdminFile", 1)[0];
  assert.equal(uploadBlock.includes("clearStoredUploadKey(storageName);\n    return result;"), true);
  assert.equal(uploadBlock.includes("catch (error)"), true);
  assert.equal(uploadBlock.includes("clearStoredUploadKey(storageName);\n    throw error;"), false);
});
