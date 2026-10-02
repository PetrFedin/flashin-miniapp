import { QueryClient } from "@tanstack/react-query";

export const adminQueryKeys = Object.freeze({
  session: () => ["admin", "session"],
  products: () => ["admin", "products"],
  orders: () => ["admin", "orders"],
  audit: () => ["admin", "audit"],
  lowStock: () => ["admin", "low-stock"],
  abandonedCarts: () => ["admin", "abandoned-carts"],
});

function shouldRetry(failureCount, error) {
  const status = Number(error?.status || 0);
  if (status >= 400 && status < 500) return false;
  return failureCount < 1;
}

export function createAdminQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 0,
        gcTime: 60 * 1000,
        retry: shouldRetry,
        refetchOnMount: "always",
        refetchOnReconnect: "always",
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
}

export function clearAdminServerState(queryClient) {
  queryClient.clear();
}
