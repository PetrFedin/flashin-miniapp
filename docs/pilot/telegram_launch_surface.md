# Telegram pilot launch-surface gate

The Telegram provider probe must prove the user-facing Mini App entry point, not only that the Bot API token is valid.

## Required BotFather/default menu configuration

For the production pilot:

1. Configure the bot's default menu button as a **Web App**.
2. Set its URL to the exact production `MINI_APP_URL` (normally `https://mini.flashin.store`).
3. Keep the application's `/start` inline Web App button enabled as a second launch path; it uses the same `MINI_APP_URL` configuration.
4. Optionally configure the bot's Main Mini App/profile **Open App** entry point as well. The probe records `has_main_web_app`, but the mandatory release condition is the exact default menu Web App URL because that URL can be read back and verified through the Bot API.

## Idempotent operator configuration

The repository includes `scripts/configure_telegram_launch_surface.py` to converge the bot's **default** menu button on the configured production Mini App without blindly writing provider state. It always reads the current default button first.

A read-only check requires only the normal production Telegram environment:

```bash
python3 scripts/configure_telegram_launch_surface.py
```

If the existing menu already matches `MINI_APP_URL` and `TELEGRAM_MENU_BUTTON_TEXT` (default `Open FLASHIN`), the command returns GO without a provider mutation. If drift is detected it fails closed. Apply the change only with an explicit operator acknowledgement:

```bash
python3 scripts/configure_telegram_launch_surface.py --acknowledge-provider-change
```

The acknowledged path calls Telegram `setChatMenuButton` for the default button and then immediately calls `getChatMenuButton` again. Success is reported only when the read-back type, text and normalized HTTPS URL match the requested configuration. Output is bounded and does not contain the bot token, configured URL or remote drift value.

## What the live provider probe verifies

`scripts/check_telegram_bot.py` performs two read-only Bot API calls:

- `getMe` verifies the bot credential/identity and records whether a Main Mini App exists;
- `getChatMenuButton` without `chat_id` reads the bot's default menu button.

The probe is GO only when:

- Telegram returns a valid bot identity;
- the default menu button type is `web_app`;
- its `web_app.url`, normalized for hostname case and a trailing slash, exactly matches `MINI_APP_URL`;
- the expected URL is HTTPS on the standard HTTPS port.

The probe does not print the token, bot ID, username, configured URL or mismatching remote URL. `check_integrations.py` further collapses probe output to status-only signed provider evidence.

## Prove real Telegram authentication on the deployed stack

A correct menu button still does not prove that a real Telegram client can authenticate against the deployed API. Before pilot admission, open the production Mini App from the allowlisted pilot Telegram account and capture the current `window.Telegram.WebApp.initData` value from that live session. Keep it ephemeral: do not paste it into Git, lifecycle JSON, screenshots, shell history, tickets or chat logs.

Load the value into the current shell without putting it in the command line, set the expected allowlisted Telegram user ID and production API URL, then run the guarded smoke:

```bash
read -r -s TELEGRAM_INIT_DATA
export TELEGRAM_INIT_DATA
export TELEGRAM_EXPECTED_USER_ID='<allowlisted numeric Telegram id>'
export API_PUBLIC_URL='https://api.flashin.store'
python3 scripts/telegram_real_auth_smoke.py --acknowledge-customer-provisioning
unset TELEGRAM_INIT_DATA TELEGRAM_EXPECTED_USER_ID
```

The acknowledgement is mandatory because `/api/auth/telegram` may create or update the pilot customer's CRM profile. The smoke does **not** create an order or payment. It submits the live signed `initData` to `/api/auth/telegram`, keeps the returned customer JWT in memory only, calls `/api/auth/me`, and verifies that the authenticated identity matches the expected pilot Telegram ID.

On success it writes a sanitized `0600` evidence file below `docs/pilot/evidence/` containing only booleans, the scenario name and observation timestamp. The raw `initData`, JWT, Telegram ID, username and provider bodies are never written to the evidence file or stdout. The directory is intentionally Git-ignored and excluded from Docker build context; the Release workflow additionally fails if any file under it has been force-added to Git.

Use the emitted evidence path and SHA-256 when preparing the `telegram_real_auth` scenario in `docs/pilot/live_lifecycle_input.json`. Keep the evidence file local/private; do not commit it.

## Why this is part of pilot admission

A valid bot token alone can coexist with a stale BotFather menu URL. In that state every backend/payment/fulfillment test can be green while a pilot user opens an old or wrong Mini App. The launch-surface check closes that gap before signed provider evidence can be GO.

The deployed live-auth smoke closes the next gap: it proves that Telegram's signed client payload is accepted by the production authentication endpoint and yields the expected customer session. It remains separate from payment and fulfillment evidence, which must still be captured by the guarded real-provider lifecycle.
