
# Security Hardening

To maintain the security of the FLASHIN Mini App:

- Configure a strict Content Security Policy (CSP) to restrict sources of scripts, styles, and images.
- Use HTTPS everywhere and enable HSTS.
- Store secrets (e.g. API keys, database credentials) outside the codebase, in environment variables or secret management services.
- Implement input validation and sanitisation on both client and server to prevent injection attacks.
- Regularly audit dependencies for known vulnerabilities and update them.
- Limit access to admin routes using JWT authentication and role-based access control.
