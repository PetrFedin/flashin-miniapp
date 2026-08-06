BEGIN;

CREATE TABLE IF NOT EXISTS platform_idempotency_keys (
    scope text NOT NULL CHECK (char_length(scope) BETWEEN 1 AND 100),
    idempotency_key text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 200),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-fA-F]{64}$'),
    state text NOT NULL CHECK (state IN ('processing', 'completed')),
    response jsonb,
    claimed_at timestamptz NOT NULL,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (scope, idempotency_key),
    CHECK (expires_at > claimed_at),
    CHECK ((state = 'processing' AND completed_at IS NULL) OR (state = 'completed' AND completed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS platform_idempotency_expiry_idx ON platform_idempotency_keys (expires_at);

CREATE TABLE IF NOT EXISTS platform_telegram_update_claims (
    bot_id text NOT NULL CHECK (char_length(bot_id) BETWEEN 1 AND 100),
    update_id bigint NOT NULL CHECK (update_id >= 0),
    claimed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (bot_id, update_id),
    CHECK (expires_at > claimed_at)
);
CREATE INDEX IF NOT EXISTS platform_telegram_update_claims_expiry_idx ON platform_telegram_update_claims (expires_at);

CREATE TABLE IF NOT EXISTS platform_inventory_items (
    sku text PRIMARY KEY CHECK (char_length(sku) BETWEEN 1 AND 200),
    on_hand bigint NOT NULL DEFAULT 0 CHECK (on_hand >= 0),
    reserved bigint NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (reserved <= on_hand)
);

CREATE TABLE IF NOT EXISTS platform_inventory_reservations (
    reservation_id text PRIMARY KEY CHECK (char_length(reservation_id) BETWEEN 1 AND 200),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-fA-F]{64}$'),
    status text NOT NULL CHECK (status IN ('active', 'released', 'sold')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_inventory_reservation_items (
    reservation_id text NOT NULL REFERENCES platform_inventory_reservations (reservation_id) ON DELETE RESTRICT,
    sku text NOT NULL REFERENCES platform_inventory_items (sku) ON DELETE RESTRICT,
    quantity bigint NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (reservation_id, sku)
);
CREATE INDEX IF NOT EXISTS platform_inventory_reservation_items_sku_idx ON platform_inventory_reservation_items (sku);

COMMIT;
