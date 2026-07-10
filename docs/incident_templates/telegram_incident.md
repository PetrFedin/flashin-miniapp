# Incident: Telegram / Bot / Mini App

## Symptoms

- Bot does not respond.
- Mini App does not open.
- Auth fails.
- MainButton does not work.

## Immediate actions

1. Check BotFather token.
2. Check BotFather domain.
3. Restart bot:
   ```bash
   docker compose restart bot
   ```
4. Check backend auth logs.
5. Test inside Telegram mobile, not browser.

## Recovery

- Re-set domain via BotFather.
- Verify `MINI_APP_URL`.
- Verify `TELEGRAM_BOT_TOKEN`.
