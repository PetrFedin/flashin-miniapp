# BotFather domain setup

## 1. Open BotFather

In Telegram open:

```text
@BotFather
```

## 2. Set Mini App domain

Run:

```text
/setdomain
```

Choose your bot.

Enter:

```text
mini.flashin.store
```

## 3. Check bot token

`.env`

```env
TELEGRAM_BOT_TOKEN=...
BOT_TOKEN=...
MINI_APP_URL=https://mini.flashin.store
```

## 4. Restart bot

```bash
docker compose restart bot
```

## 5. Test

1. Open FLASHIN bot.
2. `/start`
3. Press `🛍 Открыть магазин FLASHIN`.
4. Mini App must open inside Telegram, not external browser.
5. If auth error appears, check that the domain in BotFather exactly matches Mini App URL.
