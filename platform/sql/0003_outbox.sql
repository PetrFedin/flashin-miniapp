BEGIN;

CREATE TABLE IF NOT EXISTS platform_outbox (
    event_id text PRIMARY KEY CHECK (char_length(event_id) BETWEEN 1 AND 200),
    topic text NOT NULL CHECK (char_length(topic) BETWEEN 1 AND 100),
    partition_key text NOT NULL CHECK (char_length(partition_key) BETWEEN 1 AND 200),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-fA-F]{64}$'),
    payload jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'processing', 'sent', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL,
    locked_by text,
    locked_until timestamptz,
    last_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    sent_at timestamptz,
    dead_at timestamptz,
    CHECK ((status = 'processing' AND locked_by IS NOT NULL AND locked_until IS NOT NULL) OR status <> 'processing'),
    CHECK ((status = 'sent' AND sent_at IS NOT NULL) OR status <> 'sent'),
    CHECK ((status = 'dead' AND dead_at IS NOT NULL) OR status <> 'dead')
);

CREATE INDEX IF NOT EXISTS platform_outbox_delivery_idx
    ON platform_outbox (available_at, created_at, event_id)
    WHERE status IN ('pending', 'processing');

CREATE INDEX IF NOT EXISTS platform_outbox_lock_expiry_idx
    ON platform_outbox (locked_until)
    WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS platform_outbox_topic_status_idx
    ON platform_outbox (topic, status, created_at);

COMMIT;
