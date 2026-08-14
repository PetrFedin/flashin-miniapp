# FLASHIN rich catalog pilot operations

This runbook defines the pilot boundary for the enriched product-card, showroom, demand-request and sharing capabilities.

## Product card authority

The Admin rich-card editor is the operator source for product merchandising data:

- title, slug, brand, category and description;
- current price and old/display price;
- material and season;
- active/inactive publication state;
- badges such as bestseller, exclusive, new season, sale, outlet, drop and limited;
- display `grid_rank` (smaller values appear earlier);
- product and variant MoySklad identifiers;
- sizes, colors and physical local stock;
- image order, HTTPS video links and external availability links;
- manual related-product / complete-the-look links;
- showroom-fitting availability.

Physical stock remains guarded by `inventory.write` and must pass the inventory service invariants. A catalog editor without inventory permission must not create positive stock or alter existing physical stock.

## Availability states

`in_stock`, `preorder`, `made_to_order` and `out_of_stock` are client-visible merchandising states.

Normal checkout remains restricted to a variant with positive **local available quantity**. External availability, `preorder`, or `made_to_order` never bypass the existing reservation/payment inventory guard.

For zero-local-stock products configured as `preorder` or `made_to_order`, the Mini App exposes a separate durable demand-request lane. That lane:

- requires an authenticated customer;
- requires the request type to match the configured merchandising status;
- refuses creation when local available stock is positive;
- is idempotent for the same active customer/product/variant/type combination;
- stores requested size, color, quantity and customer notes;
- creates no `Order`, `Payment` or `InventoryMovement` and reserves no stock;
- lets the customer list and cancel their own requests;
- lets authorized operators move requests through `requested -> contacted -> confirmed`, or cancel them.

A confirmed demand request is still not a paid order and must not be represented as a guaranteed stock reservation, delivery date or completed sale.

## Demand operator permissions

The dedicated permissions are `demand.read` and `demand.write`.

- default manager: read/write;
- default support: read/write;
- default warehouse: neither;
- owner: wildcard access.

These permissions are intentionally separate from `orders.write`, `fulfillment.write` and `inventory.write`. Custom `admin_role_permissions` rows replace role defaults completely and therefore must explicitly include demand permissions when the role needs this queue.

## Pricing and sale metadata

`Product.price` is the authoritative checkout price. `old_price`, sale badges, `sale_starts_at` and `sale_ends_at` are merchandising/display data in the current pilot.

Operators must not treat sale dates as an automatic pricing scheduler. Any future scheduled-price engine must update authoritative pricing transactionally and receive its own checkout/refund regression coverage before launch.

## Media

Image uploads remain restricted to the hardened image pipeline. Rich-card save preserves a managed storage key only when an image URL belongs to the configured FLASHIN media base. External image URLs never receive a fabricated managed storage key.

Video is represented as an HTTPS/CDN media URL in the current pilot. Do not weaken the image-upload allowlist to accept arbitrary video binaries.

## External availability

External availability is advisory. It can show where an item is available outside FLASHIN and may include source, URL, status and display price.

External stock is **not** local stock and cannot be reserved through the FLASHIN cart. A client follows the external HTTPS link explicitly.

## Reviews and ratings

One authenticated customer has one rating/review record per product. Product operators can publish/hide review records through the moderation queue. Public catalog responses do not expose customer id, Telegram id, username, email or phone for feedback entries.

## Showroom appointments

Pilot fitting appointments:

- require a timezone-aware client timestamp;
- are normalized to UTC for persistence;
- are exactly 30 minutes;
- start only on `:00` or `:30` boundaries;
- can be booked at most 90 days ahead;
- reserve a unique active UTC slot;
- follow `requested -> confirmed -> completed` or cancellation transitions;
- release the active slot when cancelled or completed.

Support may operate the showroom queue through `showroom.read` / `showroom.write` without receiving product-editing or inventory permissions.

## Product sharing

When `TELEGRAM_BOT_USERNAME` is configured with the real BotFather username, product sharing uses a Telegram Main Mini App deep link:

`https://t.me/<bot_username>?startapp=product_<product_id>`

The Mini App accepts both Telegram start parameters and the web fallback `?product=<product_id>` and opens the exact product. Production startup fails closed when the bot username required for product deep links is missing or is a placeholder.

## Pilot acceptance checks

Before authorizing pilot use of the rich catalog:

1. Alembic is at the exact repository head and all catalog migrations apply successfully.
2. Full backend, frontend, Admin, browser E2E, integrated E2E and Docker/rollback CI pass on one exact commit.
3. Admin can create a controlled product, edit merchandising, stock and external availability under RBAC, and the same data appears in the Mini App.
4. Mini App filter/sort, wishlist, local-stock cart, feedback and showroom booking work through real FastAPI/PostgreSQL.
5. A zero-stock preorder/made-to-order product creates only a demand request; real-stack E2E verifies cart/order/inventory remain unchanged.
6. Manager/support can operate demand requests without receiving warehouse or payment/refund authority.
7. A support-only operator can manage showroom appointments without product/inventory access.
8. Feedback moderation hides/publishes records without surfacing customer identity fields.
9. Telegram product sharing opens the exact product through `startapp` using the real configured bot username.
10. Real provider and protected-main launch gates remain independently required; catalog CI does not authorize real-money pilot launch by itself.
