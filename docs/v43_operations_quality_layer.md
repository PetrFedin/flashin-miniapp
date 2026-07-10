# FLASHIN v43 — operations quality layer

## Added

### Self diagnostics

Endpoint:

```text
GET /api/diagnostics
```

Checks:

- database;
- required environment values;
- YooKassa configuration;
- MoySklad configuration;
- media storage;
- search mode.

### Environment validator

```bash
python scripts/validate_env.py
```

Fails if mandatory environment variables are missing or still use weak defaults.

### OpenAPI snapshot

```bash
python scripts/generate_openapi_snapshot.py
```

Writes:

```text
docs/openapi_snapshot.json
```

### Release notes generator

```bash
python scripts/generate_release_notes.py
```

Writes:

```text
docs/generated_release_notes.md
```

### Status page scaffold

```text
deploy/statuspage/index.html
```

Simple static status page scaffold.

## Why v43 matters

v42 made deployment safer. v43 makes support and handover easier:

```text
diagnose
validate env
snapshot API
generate release notes
show status page
```
