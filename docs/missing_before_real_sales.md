# FLASHIN: remaining gates before real sales

This is the current blocker register for **real commercial sales**. It must not
be used to block a provider-disabled public production deployment where checkout,
payment execution and MoySklad execution are explicitly disabled.

## Code-level status

The production codebase already contains and tests:

- protected Telegram/customer and Admin authentication;
- catalog, cart, deterministic pricing, promotions, loyalty and referrals;
- idempotent checkout and payment creation;
- YooKassa webhook/reconciliation and payment circuit-breaker paths;
- item-level financial refund allocation;
- physical reverse logistics separated from financial settlement;
- resalable / damaged / quarantine disposition authority;
- order-linked inventory reserve/commit/release ledger;
- fulfillment, shipment, tracking and delivery completion;
- support, privacy, notifications, webhooks, business events and scheduler operations;
- shared Redis production rate limiting;
- non-root least-privilege application containers;
- monitoring, signed backup/restore and signed full-release rollback;
- provider-disabled production capability modes;
- a controlled first-20-order runtime with STOP/review conditions;
- integrated browser E2E through Mini App, FastAPI, PostgreSQL and Admin;
- Security gates including CodeQL, image/SBOM scanning, secret scanning and
  HIGH/CRITICAL dependency scanning.

Release evidence is intentionally **not hard-coded to an old SHA in this file**.
The launch process must bind signed governance/readiness evidence to the exact
current `main` release being deployed.

## Provider-disabled production

A real public production environment may run before real sales when all of the
following remain authoritative:

- `COMMERCIAL_CHECKOUT_ENABLED=false`;
- `PAYMENTS_MODE=disabled`;
- `MOYSKLAD_MODE=disabled`;
- `MOYSKLAD_ORDER_EXPORT_ENABLED=false`;
- `PILOT_RUNTIME_ENFORCED=false`.

This stage proves real hosting, Telegram, authentication, catalog, cart, Admin,
monitoring, search/media configuration and operations without moving money or
creating external commerce documents.

## Mandatory gates before controlled real sales

Real checkout/payment remains **NO-GO** until all relevant gates below are
closed for one exact release and one exact production configuration:

1. Production secrets are installed outside the repository for every enabled
   service.
2. Public Mini App, API and Admin domains resolve correctly and serve valid
   HTTPS certificates.
3. Terms of sale, privacy policy, consent text, return/refund rules and seller
   details are final and public.
4. Named business, operations, technical, legal and support owners are recorded;
   on-call escalation and an external alert receiver are active.
5. GitHub `main` protection is applied and verified: pull request required,
   strict trusted CI checks, no force pushes/deletions, administrator/ruleset
   bypass policy explicitly controlled, conversations resolved.
6. GitHub Dependency Graph is enabled so differential dependency review can run.
   Until then, mandatory Trivy HIGH/CRITICAL scanning remains a fallback, not a
   substitute for closing the repository setting.
7. A fresh signed repository-governance report binds the exact `main` release
   to successful trusted CI and Security evidence.
8. Current and previous immutable releases are promoted and independently
   verifiable.
9. Signed strict provider evidence and live-readiness evidence pass against the
   deployed public environment.
10. A production-host backup/restore and rollback drill completes with retained
    signed evidence and approved RTO/RPO.
11. Signed live lifecycle evidence proves the enabled external paths, including
    real Telegram authentication and each provider being activated.
12. Payment sandbox proves redirect/return, duplicate webhook idempotency,
    refund and reconciliation before payment live mode.
13. MoySklad activation proves sellable-store sync and, before outbound export,
    order/return/disposition behavior with separate sellable/damaged/quarantine
    authority.
14. All live-pilot checklist steps required for the selected capability set are
    complete with no `todo` or `failed` state.
15. The signed admission manifest is valid for the exact release/configuration.
16. The controlled commerce runtime is armed only for the explicit pilot
    allowlist and maximum 20 orders.

Raw Telegram initData, GitHub tokens and provider secrets must never be stored in
evidence.

## External issues that must remain visible

Repository/code cleanup must not hide the remaining external launch work:

- #196 — GitHub Dependency Graph / differential dependency review;
- #86 — external credentials and business sign-off;
- #102 — live external preflight / admission evidence;
- #119 — controlled first-20-order external launch evidence.

These issues are closed only by real external evidence, not by internal CI.

## Mandatory gates after the first 20 orders

Mass launch remains forbidden until:

- all 20 pilot slots are reconciled against PostgreSQL order, payment, refund
  allocation, physical return and inventory records;
- no unresolved STOP/review-required stock, payment, refund, notification,
  fulfillment or provider incident remains;
- Finance confirms settlements/refunds and amounts;
- Operations confirms inventory, pick/pack, delivery and support outcomes;
- Legal/privacy and retention handling are confirmed;
- backup restoration and rollback evidence are retained outside the application
  host;
- the final signed pilot decision is GO and a separate mass-launch approval is
  issued.

A successful code CI run never substitutes for deployed provider, repository
governance, legal, operational or financial evidence.
