import assert from "node:assert/strict";
import test from "node:test";

import {
  clearCustomerToken,
  getCustomerToken,
  hasCustomerToken,
  setCustomerToken,
} from "./authSession.js";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

function installStorage() {
  globalThis.sessionStorage = new MemoryStorage();
  globalThis.localStorage = new MemoryStorage();
  clearCustomerToken();
}

test("customer bearer is stored in sessionStorage while localStorage receives only a presence marker", () => {
  installStorage();
  const token = "header.payload.signature";

  setCustomerToken(token);

  assert.equal(getCustomerToken(), token);
  assert.equal(globalThis.sessionStorage.getItem("flashin_customer_session_token"), token);
  assert.equal(globalThis.localStorage.getItem("flashin_token"), "session");
  assert.notEqual(globalThis.localStorage.getItem("flashin_token"), token);
  assert.equal(hasCustomerToken(), true);
});

test("clearing customer auth removes both the session bearer and compatibility marker", () => {
  installStorage();
  setCustomerToken("header.payload.signature");

  clearCustomerToken();

  assert.equal(getCustomerToken(), "");
  assert.equal(globalThis.sessionStorage.getItem("flashin_customer_session_token"), null);
  assert.equal(globalThis.localStorage.getItem("flashin_token"), null);
  assert.equal(hasCustomerToken(), false);
});

test("pre-session-storage local bearer is deleted instead of being trusted", () => {
  installStorage();
  globalThis.localStorage.setItem("flashin_token", "legacy.raw.bearer");

  assert.equal(getCustomerToken(), "");
  assert.equal(globalThis.localStorage.getItem("flashin_token"), null);
});
