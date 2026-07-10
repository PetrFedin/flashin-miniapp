
# Logging & Monitoring

To ensure reliability and observability in production, we recommend integrating the following services:

- **Structured logging** using Python's `logging` module with JSON or key-value formatting. Logs should include request IDs, user IDs, and timestamps.
- **Sentry** for error tracking and alerting. Configure Sentry DSN in environment variables and wrap key areas of the codebase.
- **Prometheus** for metrics collection, combined with **Grafana** dashboards. Expose custom metrics such as number of orders, conversion rate, and payment failures.
- **Health checks**: `/health` and `/ready` endpoints should return appropriate statuses for load balancers and uptime monitoring.

See `security.md` for details on securing sensitive logs.
