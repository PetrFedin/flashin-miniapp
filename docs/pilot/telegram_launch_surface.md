# Telegram pilot launch-surface gate

The Telegram provider probe must prove the user-facing Mini App entry point, not only that the Bot API token is valid.

## Required BotFather/default menu configuration

For the production pilot:

1. Configure the bot's default menu button as a **Web App**.
2. Set its URL to the exact production `MINI_APP_URL` (normally `https://mini.flashin.store`).
3. Keep the application's `/start` inline Web App button enabled as a second launch path; it uses the same `MINI_APP_URL` configuration.
4. Optionally configure the bot's Main Mini App/profile **Open App** entry point as well. The probe records `has_main_web_app`, but the mandatory release condition is the exact default menu Web App URL because that URL can be read back and verified through the Bot API.

## What the live probe verifies

`scripts/check_telegram_bot.py` performs two read-only Bot API calls:

- `getMe` verifies the bot credential/identity and records whether a Main Mini App exists;
- `getChatMenuButton` without `chat_id` reads the bot's default menu button.

The probe is GO only when:

- Telegram returns a valid bot identity;
- the default menu button type is `web_app`;
- its `web_app.url`, normalized for hostname case and a trailing slash, exactly matches `MINI_APP_URL`;
- the expected URL is HTTPS on the standard HTTPS port.

The probe does not print the token, bot ID, username, configured URL or mismatching remote URL. `check_integrations.py` further collapses probe output to status-only signed provider evidence.

## Why this is part of pilot admission

A valid bot token alone can coexist with a stale BotFather menu URL. In that state every backend/payment/fulfillment test can be green while a pilot user opens an old or wrong Mini App. The launch-surface check closes that gap before signed provider evidence can be GO.

This check remains a provider configuration proof, not a substitute for the deployed Telegram WebApp authentication/lifecycle E2E. The final pilot still requires a real Telegram launch and signed `initData` path through the deployed Mini App and API.
