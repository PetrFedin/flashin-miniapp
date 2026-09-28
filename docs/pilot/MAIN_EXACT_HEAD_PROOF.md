# Main exact-head production proof

This document exists only to force an ordinary GitHub branch update and exact-SHA CI/Security proof on `main` after production-hardening promotion.

## Proven production lineage

- Production hardening authority: `08c9b82ebeeccd5b21abb418ca47e1e9ba5bd1fd`
- Main promotion boundary before this proof: `e5cedf9e84d6cb34ba7d6e31bfe72b2e028eebcf`
- The two main-only release commits between those SHAs changed no repository files; the main tree therefore remained identical to the fully proven hardening tree.

## Acceptance

The merge commit for this proof is acceptable only if the resulting exact `main` SHA completes:

- CI: backend, frontend, admin, browser-e2e, integrated-e2e, docker;
- Security: CodeQL, runtime image scans, secret scan, dependency vulnerability scan, dependency review/fallback;
- production Docker proof including least-privilege runtime, Redis persistence, signed backup/restore, signed rollback and production Compose isolation.

No runtime or business logic is changed by this document.
