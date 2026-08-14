# Admin Onboarding

## First login

1. Open admin URL.
2. Login with issued credentials and the one-time TOTP code when 2FA is enabled.
3. Change password if required.
4. Verify role and effective permissions returned by the Admin session.

## Role boundary for pilot operations

- `orders.write` controls financial/order mutations such as refund approval and generic order transitions.
- `fulfillment.write` controls physical picklist, packing, shipment and delivery transitions.
- `showroom.read` / `showroom.write` control fitting-appointment operations without granting product or financial mutations.
- `demand.read` / `demand.write` control non-financial `preorder` / `made_to_order` customer demand requests. They do not authorize order creation, payment, refund or stock reservation.
- The default `warehouse` role has `fulfillment.write` but does **not** have `orders.write` or demand permissions.
- The default `support` role has showroom and demand permissions but does not receive financial order-write authority.
- The default `manager` role has fulfillment, showroom and demand permissions in addition to catalog/order responsibilities.
- If `admin_role_permissions` contains custom rows for a role, those rows replace defaults completely. A custom role must explicitly include every required permission such as `fulfillment.write`, `showroom.read/write` or `demand.read/write` before that account is used for the corresponding pilot operation.

## Daily routine

1. Check new orders.
2. Check paid orders.
3. Check fulfillment tasks.
4. Check new preorder / made-to-order demand requests and update them only through their dedicated queue.
5. Check showroom appointments.
6. Check support tickets.
7. Check MoySklad conflicts.
8. Check SLA overdue.
9. Check refunds.

## Demand request rules

- `preorder` / `made_to_order` demand is a customer intent record, not a paid order.
- A new demand request is accepted only while local available stock is zero and the product merchandising status matches the requested demand type.
- Mark `requested -> contacted -> confirmed` as the team follows up; cancellation is separate and terminal for the active request.
- Do not promise a paid reservation, delivery date or stock allocation based only on a demand-request status.
- When local stock becomes available, the normal cart/payment/inventory-reservation flow remains authoritative.

## Do not

- Do not manually mark order paid without payment verification.
- Do not reduce stock below reserved quantity.
- Do not grant `orders.write` to warehouse merely to enable picklist operations; use `fulfillment.write`.
- Do not use `orders.write` to process preorder interest; use `demand.write`.
- Do not convert a zero-stock demand request into a normal paid order by bypassing the inventory guard.
- Do not approve refund without checking order and payment.
- Do not edit legal pages without approval.
