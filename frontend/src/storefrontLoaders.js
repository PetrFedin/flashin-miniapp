function resultValue(result, fallback) {
  return result.status === "fulfilled" ? result.value : fallback;
}

function failureMessage(label, result) {
  if (result.status === "fulfilled") return null;
  const detail = result.reason?.message || "неизвестная ошибка";
  return `${label}: ${detail}`;
}

const SAFE_CAPABILITY_FALLBACK = {
  commercial_checkout: { enabled: false },
  payments: { enabled: false, mode: "disabled", provider: null },
};

export async function loadStorefrontBootstrap(api) {
  const [products, cart] = await Promise.all([
    api.listProducts(),
    api.getCart(),
  ]);
  const [capabilitiesResult, looksResult, wishlistResult] = await Promise.allSettled([
    api.getPlatformCapabilities(),
    api.listLooks(),
    api.listWishlist(),
  ]);

  return {
    products,
    cart,
    capabilities: resultValue(capabilitiesResult, SAFE_CAPABILITY_FALLBACK),
    looks: resultValue(looksResult, []),
    wishlist: resultValue(wishlistResult, []),
    warnings: [
      failureMessage("Режим покупок", capabilitiesResult),
      failureMessage("Образы", looksResult),
      failureMessage("Избранное", wishlistResult),
    ].filter(Boolean),
  };
}

const PROFILE_SECTIONS = [
  ["profile", "Профиль", "getProfile", null],
  ["loyalty", "История баллов", "myLoyalty", []],
  ["referral", "Реферальный код", "myReferralCode", null],
  ["timeline", "История действий", "getTimeline", []],
  ["tickets", "Поддержка", "listSupportTickets", []],
  ["privacy", "Запросы по данным", "listPrivacyRequests", []],
  ["wishlist", "Избранное", "listWishlist", []],
  ["orders", "Заказы", "listOrders", []],
];

export async function loadProfileSections(api) {
  const results = await Promise.allSettled(
    PROFILE_SECTIONS.map(([, , method]) => api[method]()),
  );
  const data = {};
  const warnings = [];

  PROFILE_SECTIONS.forEach(([key, label, , fallback], index) => {
    const result = results[index];
    data[key] = resultValue(result, fallback);
    const warning = failureMessage(label, result);
    if (warning) warnings.push(warning);
  });

  return { data, warnings };
}
