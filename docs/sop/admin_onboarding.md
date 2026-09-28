# Admin Onboarding

## First login

1. Open admin URL.
2. Login with issued credentials and the one-time TOTP code when 2FA is enabled.
3. Change password if required.
4. Verify role and effective permissions returned by the Admin session.

## Role boundary for pilot operations

- `orders.write` controls financial/order mutations such as refund approval and generic order transitions.
- `fulfillment.write` controls physical picklist, packing, shipment and delivery transitions.
- The default `warehouse` role has `fulfillment.write` but does **not** have `orders.write`.
- The default `manager` role has both permissions.
- If `admin_role_permissions` contains custom rows for a role, those rows replace defaults completely. A custom warehouse/manager role must explicitly include `fulfillment.write` before that account is used for pilot fulfillment.

## Daily routine

1. Check new orders.
2. Check paid orders.
3. Check fulfillment tasks.
4. Check support tickets.
5. Check MoySklad conflicts.
6. Check SLA overdue.
7. Check refunds.

## Do not

- Do not manually mark order paid without payment verification.
- Do not reduce stock below reserved quantity.
- Do not grant `orders.write` to warehouse merely to enable picklist operations; use `fulfillment.write`.
- Do not approve refund without checking order and payment.
- Do not edit legal pages without approval.
