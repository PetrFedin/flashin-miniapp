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
