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


test("admin downloads reject a response if the admin session changes mid-flight", () => {
  assert.equal(source.includes("const tokenAtStart = getAdminToken()"), true);
  assert.equal(source.includes("headers: authHeaders(true, {}, tokenAtStart)"), true);
  assert.equal(source.match(/assertAdminSessionUnchanged\(true, tokenAtStart\)/g)?.length >= 2, true);
});
