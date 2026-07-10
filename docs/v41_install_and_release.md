# v41 install and release

## Local install

```bash
./scripts/install.sh
```

## Manual

```bash
cp .env.local.example .env
make preflight
make build
make migrate
make up
make health
```

## Workers

```bash
make workers
```

## Scheduler

```bash
docker compose --profile scheduler up -d scheduler
```

## CI/CD

GitHub Actions are included. Add secrets in your repository:

```text
DOCKER_USERNAME
DOCKER_PASSWORD
DEPLOY_HOST
DEPLOY_KEY
```

Then adapt `.github/workflows/release.yml` for your server.
