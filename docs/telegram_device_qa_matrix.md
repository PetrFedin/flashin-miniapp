# Telegram device QA matrix

Run this checklist on real devices.

## iOS Telegram

- [ ] Mini App opens from bot.
- [ ] `WebApp.ready()` removes white loading screen.
- [ ] Theme colors match Telegram theme.
- [ ] Product grid scrolls smoothly.
- [ ] Product deeplink opens correct product.
- [ ] MainButton appears after add to cart.
- [ ] Checkout form keyboard does not hide button.
- [ ] YooKassa redirect returns to Mini App.
- [ ] Offline screen appears with network disabled.
- [ ] Back navigation does not break state.

## Android Telegram

- [ ] Mini App opens from bot.
- [ ] Product images lazy-load.
- [ ] Size buttons work.
- [ ] MainButton works.
- [ ] Promo apply works.
- [ ] Payment redirect works.
- [ ] Telegram haptic feedback does not crash.
- [ ] Low memory reload keeps server-side cart.
- [ ] Offline state works.
- [ ] Order history opens.

## Telegram Desktop

- [ ] Mini App opens.
- [ ] Layout remains usable in wider viewport.
- [ ] Admin is not accessible from customer app.
- [ ] Payment redirect works.
- [ ] Cart persists after reload.
- [ ] Console has no fatal errors.

## Go / No-go

No public launch if any of these fail:

- auth;
- add to cart;
- checkout;
- payment webhook;
- inventory decrement;
- duplicate webhook idempotency;
- admin order status update.
