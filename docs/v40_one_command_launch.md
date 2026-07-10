# v40 one-command local launch

## 1. Unzip

```bash
unzip flashin-miniapp-v40.zip
cd flashin-miniapp-v40
```

## 2. Configure env

```bash
cp .env.local.example .env
```

Fill:

```env
TELEGRAM_BOT_TOKEN=
JWT_SECRET=
ADMIN_EMAIL=
ADMIN_PASSWORD=
```

## 3. Launch

```bash
make init
```

## 4. Check

```bash
make health
python tests/e2e_smoke.py
```

## 5. Open

```text
http://localhost:5173
http://localhost:5174
http://localhost:8000/docs
```
