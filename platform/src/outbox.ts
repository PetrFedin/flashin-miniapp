import { createHash } from "node:crypto";
import { DomainError, invariant } from "./errors.js";
import type { SqlClient, SqlDatabase, SqlValue } from "./persistence.js";

export type OutboxStatus = "pending" | "processing" | "sent" | "dead";

export interface OutboxEnqueueInput {
  readonly eventId: string;
  readonly topic: string;
  readonly partitionKey: string;
  readonly payload: unknown;
  readonly availableAtIso: string;
  readonly nowIso: string;
}

export interface OutboxMessage {
  readonly eventId: string;
  readonly topic: string;
  readonly partitionKey: string;
  readonly payload: unknown;
  readonly attempts: number;
}

interface OutboxIdentityRow {
  readonly fingerprint: string;
  readonly status: OutboxStatus;
}

interface OutboxClaimRow {
  readonly event_id: string;
  readonly topic: string;
  readonly partition_key: string;
  readonly payload: unknown;
  readonly attempts: number | string | bigint;
}

const INSERT_SQL = `
INSERT INTO platform_outbox (
  event_id, topic, partition_key, fingerprint, payload, status,
  attempts, available_at, created_at, updated_at
)
VALUES ($1, $2, $3, $4, $5::jsonb, 'pending', 0, $6::timestamptz, $7::timestamptz, $7::timestamptz)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id`;

const GET_SQL = `
SELECT fingerprint, status
FROM platform_outbox
WHERE event_id = $1`;

const CLAIM_SQL = `
WITH candidates AS (
  SELECT event_id
  FROM platform_outbox
  WHERE (
      status = 'pending'
      OR (status = 'processing' AND locked_until <= $1::timestamptz)
    )
    AND available_at <= $1::timestamptz
  ORDER BY available_at, created_at, event_id
  FOR UPDATE SKIP LOCKED
  LIMIT $2
)
UPDATE platform_outbox AS outbox
SET status = 'processing',
    attempts = outbox.attempts + 1,
    locked_by = $3,
    locked_until = $4::timestamptz,
    updated_at = $1::timestamptz
FROM candidates
WHERE outbox.event_id = candidates.event_id
RETURNING outbox.event_id, outbox.topic, outbox.partition_key, outbox.payload, outbox.attempts`;

const SENT_SQL = `
UPDATE platform_outbox
SET status = 'sent',
    sent_at = $4::timestamptz,
    locked_by = NULL,
    locked_until = NULL,
    last_error = NULL,
    updated_at = $4::timestamptz
WHERE event_id = $1
  AND status = 'processing'
  AND locked_by = $2
  AND locked_until > $3::timestamptz
RETURNING event_id`;

const RESCHEDULE_SQL = `
UPDATE platform_outbox
SET status = CASE WHEN attempts >= $6 THEN 'dead' ELSE 'pending' END,
    available_at = CASE WHEN attempts >= $6 THEN available_at ELSE $4::timestamptz END,
    dead_at = CASE WHEN attempts >= $6 THEN $3::timestamptz ELSE NULL END,
    locked_by = NULL,
    locked_until = NULL,
    last_error = $5,
    updated_at = $3::timestamptz
WHERE event_id = $1
  AND status = 'processing'
  AND locked_by = $2
RETURNING event_id, status`;

export class PostgresOutboxRepository {
  public constructor(private readonly database: SqlDatabase) {}

  public async enqueue(input: OutboxEnqueueInput): Promise<"created" | "replayed"> {
    return this.database.transaction((client) => this.enqueueWithClient(client, input));
  }

  public async enqueueWithClient(client: SqlClient, input: OutboxEnqueueInput): Promise<"created" | "replayed"> {
    validateEnqueueInput(input);
    const encodedPayload = canonicalJson(input.payload);
    const fingerprint = createHash("sha256")
      .update(`${input.topic}\n${input.partitionKey}\n${encodedPayload}`, "utf8")
      .digest("hex");

    const inserted = await client.query<{ readonly event_id: string }>("outbox.enqueue", INSERT_SQL, [
      input.eventId,
      input.topic,
      input.partitionKey,
      fingerprint,
      encodedPayload,
      input.availableAtIso,
      input.nowIso,
    ]);
    if (inserted.rowCount === 1) return "created";

    const current = await client.query<OutboxIdentityRow>("outbox.get", GET_SQL, [input.eventId]);
    invariant(current.rowCount === 1 && current.rows[0], "outbox.missing_after_conflict", "Outbox event disappeared after an event ID conflict");
    if (current.rows[0].fingerprint !== fingerprint) {
      throw new DomainError("outbox.idempotency_conflict", "Outbox event ID was reused with different contents", {
        eventId: input.eventId,
      });
    }
    return "replayed";
  }

  public async claimBatch(input: {
    readonly nowIso: string;
    readonly lockedUntilIso: string;
    readonly workerId: string;
    readonly limit: number;
  }): Promise<readonly OutboxMessage[]> {
    validateWorkerId(input.workerId);
    invariant(Number.isSafeInteger(input.limit) && input.limit >= 1 && input.limit <= 500, "outbox.invalid_claim_limit", "Outbox claim limit must be between 1 and 500");
    validateIncreasingDates(input.nowIso, input.lockedUntilIso, "outbox.invalid_lock_expiry");

    return this.database.transaction(async (client) => {
      const result = await client.query<OutboxClaimRow>("outbox.claim_batch", CLAIM_SQL, [
        input.nowIso,
        input.limit,
        input.workerId,
        input.lockedUntilIso,
      ]);
      return result.rows.map((row) => ({
        eventId: row.event_id,
        topic: row.topic,
        partitionKey: row.partition_key,
        payload: decodeJson(row.payload),
        attempts: toSafeInteger(row.attempts, "outbox.invalid_attempts"),
      }));
    });
  }

  public async markSent(input: {
    readonly eventId: string;
    readonly workerId: string;
    readonly nowIso: string;
    readonly sentAtIso: string;
  }): Promise<void> {
    validateEventId(input.eventId);
    validateWorkerId(input.workerId);
    validateIso(input.nowIso, "outbox.invalid_now");
    validateIso(input.sentAtIso, "outbox.invalid_sent_at");
    const result = await this.database.query<{ readonly event_id: string }>("outbox.mark_sent", SENT_SQL, [
      input.eventId,
      input.workerId,
      input.nowIso,
      input.sentAtIso,
    ]);
    invariant(result.rowCount === 1, "outbox.lost_lease", "Outbox event lease is missing, expired or owned by another worker", { eventId: input.eventId });
  }

  public async reschedule(input: {
    readonly eventId: string;
    readonly workerId: string;
    readonly nowIso: string;
    readonly availableAtIso: string;
    readonly error: string;
    readonly maxAttempts: number;
  }): Promise<"pending" | "dead"> {
    validateEventId(input.eventId);
    validateWorkerId(input.workerId);
    validateIncreasingDates(input.nowIso, input.availableAtIso, "outbox.invalid_retry_time");
    const error = input.error.trim();
    invariant(error.length >= 1 && error.length <= 2_000, "outbox.invalid_error", "Outbox error must be 1-2000 characters");
    invariant(Number.isSafeInteger(input.maxAttempts) && input.maxAttempts >= 1 && input.maxAttempts <= 100, "outbox.invalid_max_attempts", "Outbox max attempts must be between 1 and 100");

    const result = await this.database.query<{ readonly event_id: string; readonly status: "pending" | "dead" }>(
      "outbox.reschedule",
      RESCHEDULE_SQL,
      [input.eventId, input.workerId, input.nowIso, input.availableAtIso, error, input.maxAttempts],
    );
    invariant(result.rowCount === 1 && result.rows[0], "outbox.lost_lease", "Outbox event lease is missing or owned by another worker", { eventId: input.eventId });
    return result.rows[0].status;
  }
}

export function canonicalJson(value: unknown): string {
  const seen = new Set<object>();
  const normalize = (current: unknown): unknown => {
    if (current === null || typeof current === "string" || typeof current === "boolean") return current;
    if (typeof current === "number") {
      invariant(Number.isFinite(current), "outbox.non_finite_number", "Outbox payload cannot contain non-finite numbers");
      return current;
    }
    if (typeof current === "bigint" || typeof current === "undefined" || typeof current === "function" || typeof current === "symbol") {
      throw new DomainError("outbox.unserializable_payload", "Outbox payload contains a value that is not JSON serializable");
    }
    invariant(typeof current === "object", "outbox.unserializable_payload", "Outbox payload is not JSON serializable");
    invariant(!seen.has(current), "outbox.circular_payload", "Outbox payload cannot contain circular references");
    seen.add(current);
    try {
      if (Array.isArray(current)) return current.map(normalize);
      const record = current as Record<string, unknown>;
      return Object.fromEntries(Object.keys(record).sort().map((key) => [key, normalize(record[key])]));
    } finally {
      seen.delete(current);
    }
  };
  const encoded = JSON.stringify(normalize(value));
  invariant(encoded !== undefined, "outbox.unserializable_payload", "Outbox payload is not JSON serializable");
  return encoded;
}

function validateEnqueueInput(input: OutboxEnqueueInput): void {
  validateEventId(input.eventId);
  validateLabel(input.topic, "outbox.invalid_topic", "Outbox topic", 100);
  validateLabel(input.partitionKey, "outbox.invalid_partition_key", "Outbox partition key", 200);
  validateIso(input.nowIso, "outbox.invalid_now");
  const available = Date.parse(input.availableAtIso);
  invariant(Number.isFinite(available), "outbox.invalid_available_at", "Outbox available_at must be a valid ISO date");
}

function validateEventId(value: string): void {
  validateLabel(value, "outbox.invalid_event_id", "Outbox event ID", 200);
}

function validateWorkerId(value: string): void {
  validateLabel(value, "outbox.invalid_worker_id", "Outbox worker ID", 100);
}

function validateLabel(value: string, code: string, label: string, max: number): void {
  const normalized = value.trim();
  invariant(normalized.length >= 1 && normalized.length <= max, code, `${label} must be 1-${max} characters`);
}

function validateIso(value: string, code: string): number {
  const parsed = Date.parse(value);
  invariant(Number.isFinite(parsed), code, "Timestamp must be a valid ISO date", { value });
  return parsed;
}

function validateIncreasingDates(from: string, to: string, code: string): void {
  const fromMs = validateIso(from, code);
  const toMs = validateIso(to, code);
  invariant(toMs > fromMs, code, "Expiry or retry timestamp must be later than the current timestamp");
}

function decodeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    throw new DomainError("outbox.corrupt_payload", "Stored outbox payload is not valid JSON");
  }
}

function toSafeInteger(value: number | string | bigint, code: string): number {
  const parsed = Number(value);
  invariant(Number.isSafeInteger(parsed) && parsed >= 0, code, "Database returned an invalid non-negative safe integer", { value: String(value) });
  return parsed;
}
