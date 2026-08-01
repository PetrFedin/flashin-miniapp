import assert from "node:assert/strict";
import test from "node:test";

import { loadProfileSections, loadStorefrontBootstrap } from "./storefrontLoaders.js";

test("bootstrap keeps critical data when optional sections fail", async () => {
  const result = await loadStorefrontBootstrap({
    listProducts: async () => [{ id: 1 }],
    getCart: async () => ({ id: 2, items: [] }),
    listLooks: async () => { throw new Error("looks offline"); },
    listWishlist: async () => [{ id: 3 }],
  });

  assert.deepEqual(result.products, [{ id: 1 }]);
  assert.deepEqual(result.cart, { id: 2, items: [] });
  assert.deepEqual(result.looks, []);
  assert.deepEqual(result.wishlist, [{ id: 3 }]);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /Образы/);
});

test("bootstrap rejects when catalog or cart cannot load", async () => {
  await assert.rejects(
    loadStorefrontBootstrap({
      listProducts: async () => { throw new Error("catalog offline"); },
      getCart: async () => ({ id: 2 }),
      listLooks: async () => [],
      listWishlist: async () => [],
    }),
    /catalog offline/,
  );
});

test("profile loader preserves successful sections", async () => {
  const api = {
    getProfile: async () => ({ customer: { id: 1 } }),
    myLoyalty: async () => [{ id: 2 }],
    myReferralCode: async () => { throw new Error("referral offline"); },
    getTimeline: async () => [],
    listSupportTickets: async () => [{ id: 3 }],
    listPrivacyRequests: async () => [],
    listWishlist: async () => [{ id: 4 }],
    listOrders: async () => [{ id: 5 }],
  };

  const result = await loadProfileSections(api);

  assert.deepEqual(result.data.profile, { customer: { id: 1 } });
  assert.deepEqual(result.data.loyalty, [{ id: 2 }]);
  assert.equal(result.data.referral, null);
  assert.deepEqual(result.data.tickets, [{ id: 3 }]);
  assert.deepEqual(result.data.orders, [{ id: 5 }]);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /Реферальный код/);
});
