# Security Policy

## Supported branches

Security fixes are developed in short-lived branches and merged through pull requests.

| Branch | Support status |
|---|---|
| `main` | Production branch. Receives approved security fixes. |
| `develop` | Active integration branch. Receives fixes before production release. |
| Feature and maintenance branches | Supported only while an associated pull request is open. |

## Reporting a vulnerability

Do not publish a confirmed or suspected vulnerability in a public issue, discussion, pull request, commit message, or chat.

Send the report directly to the repository owner using a private communication channel. Include:

- affected component and endpoint;
- affected branch and commit SHA;
- reproducible steps;
- expected and actual result;
- severity and business impact;
- proof of concept without real customer data;
- proposed mitigation, when available.

The report must not contain production secrets, payment credentials, Telegram bot tokens, customer personal data, access tokens, session tokens, database dumps, or real payment information.

## Response process

1. The owner confirms receipt and records the report privately.
2. The issue is reproduced in an isolated environment.
3. Severity is assigned using impact and exploitability.
4. A private fix branch is created.
5. Automated tests and regression checks are added.
6. The fix is reviewed and merged through a pull request.
7. Compromised secrets are rotated before deployment.
8. The production deployment is verified.
9. A public advisory is created only when disclosure is safe and necessary.

## Severity guidance

### Critical

Examples:

- remote code execution;
- unauthenticated administrative access;
- exposure of payment credentials or Telegram bot token;
- arbitrary order or product modification;
- database compromise;
- webhook signature bypass that can mark unpaid orders as paid;
- mass disclosure of customer personal data.

### High

Examples:

- privilege escalation;
- broken object-level authorization;
- reusable authentication bypass;
- stored cross-site scripting in the admin panel;
- unauthorized refund or inventory operations;
- access to another customer's orders or addresses.

### Medium

Examples:

- limited information disclosure;
- rate-limit bypass;
- insufficient session invalidation;
- security-sensitive configuration weakness without direct exploitation.

### Low

Examples:

- missing non-critical security headers;
- low-impact information leakage;
- hardening improvements without a demonstrated exploit path.

## Mandatory security requirements

### Secrets

- Never commit `.env` files, private keys, access tokens, Telegram bot tokens, YooKassa credentials, MoySklad credentials, S3/R2 credentials, database passwords, JWT secrets, webhook secrets, or production URLs containing credentials.
- Store production secrets in the deployment platform secret manager or GitHub Actions secrets.
- Use separate credentials for local, test, staging, and production environments.
- Rotate any secret immediately after suspected disclosure.
- Use long, randomly generated values for JWT, session, webhook, and encryption secrets.

### Authentication and authorization

- Telegram `initData` must be validated on the backend using the official HMAC procedure.
- Administrative endpoints must require authenticated admin users.
- Authorization must be enforced server-side for every protected operation.
- RBAC checks must be explicit for owner, administrator, operator, support, warehouse, marketing, and read-only roles where applicable.
- Client-side UI restrictions are not authorization controls.
- Access tokens must have an expiration time and must not be logged.
- Passwords must be hashed with a modern adaptive password hash such as Argon2id or bcrypt. SHA-256, MD5, and unsalted hashes are prohibited for password storage.

### Payments and webhooks

- YooKassa webhook processing must verify event authenticity using provider-side status retrieval and idempotency checks.
- Payment status must never be trusted from the frontend.
- A paid order may be confirmed only after the backend verifies the payment with the payment provider.
- Webhook handlers must be idempotent and safe for repeated delivery.
- Refund operations must be authenticated, authorized, logged, and reconciled with the provider.
- Telegram Stars must not be used for physical merchandise unless Telegram rules explicitly permit the exact use case and the implementation has been reviewed.

### Personal data

- Collect only data required for order fulfilment, support, fraud prevention, accounting, and legal obligations.
- Do not log full addresses, phone numbers, payment identifiers, access tokens, or Telegram `initData`.
- Limit access to customer data by role.
- Define retention and deletion procedures for customer data, logs, abandoned carts, orders, and support records.
- Use encrypted transport for all production traffic.

### Database and infrastructure

- Production databases must not publish their ports to the public internet.
- Database access must be restricted to the application network and approved administrative channels.
- Production containers must run with minimum privileges and without unnecessary host mounts.
- Default credentials are prohibited in production.
- Backups must be encrypted, access-controlled, monitored, and restoration-tested.
- CORS, trusted hosts, proxy headers, and cookie settings must be explicitly configured for production domains.

### Application security

- Validate all request data using explicit schemas and constraints.
- Use `Decimal` or database numeric types for monetary values. Floating-point types are prohibited for persisted money calculations.
- Prevent mass assignment by mapping allowed fields explicitly.
- Protect file uploads with type, size, extension, and content validation.
- Apply rate limits to authentication, checkout, payment, webhook, promo-code, password, and administrative endpoints.
- Use parameterized database queries through SQLAlchemy. Raw SQL must be reviewed.
- Do not expose stack traces, internal paths, database errors, credentials, or provider responses to clients.

### Logging and audit

- Administrative changes to products, stock, orders, refunds, promotions, users, and roles must create audit records.
- Audit records should include actor, action, object type, object identifier, timestamp, request correlation identifier, and a safe summary of changes.
- Logs must exclude secrets and sensitive personal data.
- Security-relevant events must be retained according to the production retention policy.

## Pull request requirements for security-sensitive changes

A security-sensitive pull request must include:

- threat or abuse case addressed;
- affected endpoints, roles, and data;
- migration impact;
- automated tests;
- manual verification steps;
- rollback plan;
- secret rotation requirements;
- monitoring and deployment notes.

The pull request must not be merged when required CI checks fail.

## Dependency and supply-chain policy

- Dependencies must be pinned to reviewed versions.
- Dependency updates must pass backend tests and frontend/admin builds.
- Critical and high-severity dependency vulnerabilities must be triaged before production release.
- Unused dependencies must be removed.
- Build artifacts must come from trusted registries and pinned major versions or immutable digests where practical.

## Production release security checklist

Before every production release confirm that:

- repository visibility and access are appropriate;
- branch protection is enabled for `main`;
- required CI checks pass;
- production secrets are configured outside the repository;
- debug mode and seed data are disabled;
- database ports are not publicly exposed;
- webhook endpoints use HTTPS and provider verification;
- payment test credentials are not used in production;
- admin credentials have been rotated from bootstrap values;
- backups and rollback procedures are available;
- monitoring and error tracking are active;
- legal and privacy documents are published and current.

## Out of scope

The following do not qualify as security vulnerabilities unless a real security impact is demonstrated:

- missing product features;
- visual or usability defects;
- rate limits triggered by intentional stress testing without prior approval;
- reports based only on automated scanner output without reproducible impact;
- attacks that require access to an already compromised administrator account;
- social engineering against employees or customers.

## Safe testing rules

- Use local or dedicated test environments.
- Do not test against real customers or real orders without written approval.
- Do not perform denial-of-service testing.
- Do not exfiltrate, alter, or delete production data.
- Stop testing immediately after demonstrating the minimum proof required.
