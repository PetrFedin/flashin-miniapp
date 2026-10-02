import {
  getCart,
  getOrder,
  getPlatformCapabilities,
  getProduct,
  getProfile,
  getTimeline,
  listLooks,
  listOrders,
  listPrivacyRequests,
  listProducts,
  listSupportTickets,
  listWishlist,
  myLoyalty,
  myReferralCode,
  searchProducts,
} from "./api.js";
import {
  getCatalogProduct,
  listCatalogProducts,
  listMyShowroomAppointments,
  listProductFeedback,
  listIntentEligibleProducts,
  listMyProductIntents,
} from "./catalogApi.js";
import { queryKeys, queryPolicy } from "./queryClient.js";

function truth(queryKey, queryFn) {
  return { queryKey, queryFn, ...queryPolicy.businessTruth };
}

function catalog(queryKey, queryFn) {
  return { queryKey, queryFn, ...queryPolicy.catalog };
}

export const storefrontQueries = Object.freeze({
  products: () => catalog(queryKeys.products(), () => listProducts()),
  product: (productId) => truth(queryKeys.product(productId), () => getProduct(productId)),
  search: (query) => truth(queryKeys.productSearch(query), () => searchProducts(query)),
  capabilities: () => truth(queryKeys.capabilities(), () => getPlatformCapabilities()),
  looks: () => catalog(queryKeys.looks(), () => listLooks()),
  cart: () => truth(queryKeys.cart(), () => getCart()),
  wishlist: () => truth(queryKeys.wishlist(), () => listWishlist()),
  orders: () => truth(queryKeys.orders(), () => listOrders()),
  order: (orderId) => truth(queryKeys.order(orderId), () => getOrder(orderId)),
  profile: () => truth(queryKeys.profile(), () => getProfile()),
  loyalty: () => truth(queryKeys.loyalty(), () => myLoyalty()),
  referral: () => truth(queryKeys.referral(), () => myReferralCode()),
  timeline: () => truth(queryKeys.timeline(), () => getTimeline()),
  support: () => truth(queryKeys.support(), () => listSupportTickets()),
  privacy: () => truth(queryKeys.privacy(), () => listPrivacyRequests()),
  catalogGrid: (filters) => catalog(
    queryKeys.catalogGrid(filters),
    () => listCatalogProducts(filters),
  ),
  catalogDetail: (productId) => truth(
    queryKeys.catalogDetail(productId),
    () => getCatalogProduct(productId),
  ),
  catalogFeedback: (productId) => truth(
    queryKeys.catalogFeedback(productId),
    () => listProductFeedback(productId),
  ),
  appointments: () => truth(
    queryKeys.appointments(),
    () => listMyShowroomAppointments(),
  ),
  intentEligible: () => truth(
    queryKeys.intentEligible(),
    () => listIntentEligibleProducts(),
  ),
  intents: () => truth(queryKeys.intents(), () => listMyProductIntents()),
});

export async function fetchStorefrontBootstrap(queryClient) {
  const [products, cart] = await Promise.all([
    queryClient.fetchQuery(storefrontQueries.products()),
    queryClient.fetchQuery(storefrontQueries.cart()),
  ]);
  const [capabilitiesResult, looksResult, wishlistResult] = await Promise.allSettled([
    queryClient.fetchQuery(storefrontQueries.capabilities()),
    queryClient.fetchQuery(storefrontQueries.looks()),
    queryClient.fetchQuery(storefrontQueries.wishlist()),
  ]);
  return { products, cart, capabilitiesResult, looksResult, wishlistResult };
}
