import { QueryClient } from "@tanstack/react-query";

const TRUTH_GC_MS = 2 * 60 * 1000;
const CATALOG_STALE_MS = 30 * 1000;

export const queryKeys = Object.freeze({
  products: () => ["catalog", "products"],
  product: (productId) => ["catalog", "product", Number(productId)],
  productSearch: (query) => ["catalog", "search", String(query || "").trim()],
  capabilities: () => ["platform", "capabilities"],
  looks: () => ["catalog", "looks"],
  cart: () => ["customer", "cart"],
  wishlist: () => ["customer", "wishlist"],
  orders: () => ["customer", "orders"],
  order: (orderId) => ["customer", "order", Number(orderId)],
  profile: () => ["customer", "profile"],
  loyalty: () => ["customer", "loyalty"],
  referral: () => ["customer", "referral"],
  timeline: () => ["customer", "timeline"],
  support: () => ["customer", "support"],
  privacy: () => ["customer", "privacy"],
  catalogGrid: (filters) => ["catalog", "grid", filters || {}],
  catalogDetail: (productId) => ["catalog", "detail", Number(productId)],
  catalogFeedback: (productId) => ["catalog", "feedback", Number(productId)],
  appointments: () => ["customer", "showroom-appointments"],
  intentEligible: () => ["customer", "intent-eligible"],
  intents: () => ["customer", "product-intents"],
});

function shouldRetry(failureCount, error) {
  const status = Number(error?.status || 0);
  if (status >= 400 && status < 500) return false;
  return failureCount < 1;
}

export function createFlashinQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 0,
        gcTime: TRUTH_GC_MS,
        retry: shouldRetry,
        refetchOnMount: "always",
        refetchOnReconnect: "always",
        refetchOnWindowFocus: true,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export const queryPolicy = Object.freeze({
  catalog: Object.freeze({
    staleTime: CATALOG_STALE_MS,
    gcTime: 5 * 60 * 1000,
  }),
  businessTruth: Object.freeze({
    staleTime: 0,
    gcTime: TRUTH_GC_MS,
    refetchOnMount: "always",
    refetchOnReconnect: "always",
    refetchOnWindowFocus: true,
  }),
});

export function clearCustomerServerState(queryClient) {
  queryClient.removeQueries({ queryKey: ["customer"] });
}

export function commitAuthoritativeResult(queryClient, queryKey, value) {
  queryClient.setQueryData(queryKey, value);
  return value;
}
