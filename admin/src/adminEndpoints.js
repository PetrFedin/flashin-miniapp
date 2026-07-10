export const adminUxEndpoints = {
  orderDetail: (id) => `/api/admin/orders/${id}`,
  productDetail: (id) => `/api/admin/products/${id}`,
  customerDetail: (id) => `/api/admin/customers/${id}`,
  paymentReconciliation: "/api/payment-reconciliation",
  deliveryShipments: "/api/delivery-providers/shipments",
  deliveryProviders: "/api/delivery-providers",
  adminSecurityLoginEvents: "/api/admin-security/login-events",
  adminSecuritySessions: "/api/admin-security/sessions",
  moyskladSkuMatches: "/api/moysklad-deep-mapping/sku-matches",
};
