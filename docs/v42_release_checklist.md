# v42 release checklist

## Before deploy

- [ ] `.env` is production-ready.
- [ ] Secrets are in password manager / secret manager.
- [ ] Latest DB backup exists.
- [ ] Backup verification was tested.
- [ ] BotFather domain configured.
- [ ] YooKassa webhook configured.
- [ ] MoySklad sync tested.
- [ ] Legal pages finalized.
- [ ] Meilisearch configured.
- [ ] Monitoring enabled.

## Deploy

```bash
make deploy-prod
```

## After deploy

```bash
make health
python tests/e2e_smoke.py
```

## Rollback

```bash
make rollback RELEASE=previous-release.zip BACKUP=backups/flashin_xxx.sql.gz
```

## No-go conditions

- migrations fail;
- healthcheck fails;
- payment webhook fails;
- admin login fails;
- checkout fails;
- stock writeoff fails;
- refund fails.
