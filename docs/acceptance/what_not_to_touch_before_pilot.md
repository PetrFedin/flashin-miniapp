# What Not To Touch Before Pilot

Do not add new architecture before pilot.

## Do not change

- database model names;
- payment flow;
- webhook routes;
- order status names;
- inventory writeoff logic;
- fulfillment creation logic;
- Telegram auth logic;
- migration chain;
- Docker service names;
- env variable names.

## Allowed before pilot

- fix clear syntax errors;
- fix broken imports;
- fix invalid endpoint path;
- fix UI copy;
- add missing documentation;
- add missing env value;
- add mapping rules for real MoySklad data.

## Rule

If the issue does not block the 20-order pilot, do not change code.
