# Backend hardening validation

This draft validation change triggers the pull-request CI workflow against the current `main` hardening set.

Validation scope:

- payment and webhook idempotency;
- inventory invariants;
- checkout and cancellation locking;
- loyalty hold lifecycle;
- Telegram and admin authentication;
- cart concurrency controls;
- admin RBAC and input validation.

The file contains no runtime configuration and does not change application behavior.
