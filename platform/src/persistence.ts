import { DomainError, invariant } from "./errors.js";
import type { StockItem } from "./inventory.js";

export type SqlValue = string | number | bigint | boolean | null | readonly string[] | readonly number[] | readonly bigint[];

export interface SqlResult<Row> {
  readonly rows: readonly Row[];
  readonly rowCount: number;
}

export interface SqlClient {
  query<Row>(name: string, text: string, values?: readonly SqlValue[]): Promise<SqlResult<Row>>;
}

export interface SqlDatabase extends SqlClient {
  transaction<T>(operation: (client: SqlClient) => Promise<T>): Promise<T>;
}

type IdempotencyState = "processing" | "completed";

interface IdempotencyRow {
  readonly fingerprint: string;
  readonly state: IdempotencyState;
  readonly response: unknown;
}

export type IdempotencyClaim =
  | { readonly outcome: "claimed" }
  | { readonly outcome: "in_progress" }
  | { readonly outcome: "replay"; readonly response: unknown };

export interface IdempotencyClaimInput {
  readonly scope: string;
  readonly key: string;
  readonly fingerprint: string;
  readonly nowIso: string;
  readonly expiresAtIso: string;
}

const IDEMPOTENCY_CLAIM_SQL = `
INSERT INTO platform_idempotency_keys (
  scope, idempotency_key, fingerprint, state, claimed_at, updated_at, expires_at
)
VALUES ($1, $2, $3, 'processing', $4::timestamptz, $4::timestamptz, $5::timestamptz)
ON CONFLICT (scope, idempotency_key) DO UPDATE
SET fingerprint = EXCLUDED.fingerprint,
    state = 'processing',
    response = NULL,
    claimed_at = EXCLUDED.claimed_at,
    completed_at = NULL,
    updated_at = EXCLUDED.updated_at,
    expires_at = EXCLUDED.expires_at
WHERE platform_idempotency_keys.expires_at <= EXCLUDED.claimed_at
RETURNING fingerprint, state, response`;

const IDEMPOTENCY_GET_SQL = `
SELECT fingerprint, state, response
FROM platform_idempotency_keys
WHERE scope = $1 AND idempotency_key = $2`;

const IDEMPOTENCY_COMPLETE_SQL = `
UPDATE platform_idempotency_keys
SET state = 'completed',
    response = $4::jsonb,
    completed_at = $5::timestamptz,
    updated_at = $5::timestamptz
WHERE scope = $1
  AND idempotency_key = $2
  AND fingerprint = $3
  AND state = 'processing'
RETURNING fingerprint, state, response`;

const IDEMPOTENCY_ABORT_SQL = `
DELETE FROM platform_idempotency_keys
WHERE scope = $1
  AND idempotency_key = $2
  AND fingerprint = $3
  AND state = 'processing'`;

export class PostgresIdempotencyRepository {
  public constructor(private readonly database: SqlDatabase) {}

  public async claim(input: IdempotencyClaimInput): Promise<IdempotencyClaim> {
    validateIdempotencyInput(input);
    return this.database.transaction(async (client) => {
      const claimed = await client.query<IdempotencyRow>(
        "idempotency.claim",
        IDEMPOTENCY_CLAIM_SQL,
        [input.scope.trim(), input.key.trim(), input.fingerprint, input.nowIso, input.expiresAtIso],
      );
      if (claimed.rowCount === 1) return { outcome: "claimed" };

      const current = await client.query<IdempotencyRow>(
        "idempotency.get",
        IDEMPOTENCY_GET_SQL,
        [input.scope.trim(), input.key.trim()],
      );
      invariant(current.rowCount === 1 && current.rows[0], "idempotency.missing_after_conflict", "Idempotency record disappeared during claim");
      return classifyIdempotencyRow(current.rows[0], input.fingerprint);
    });
  }

  public async complete(
    input: Pick<IdempotencyClaimInput, "scope" | "key" | "fingerprint"> & { readonly response: unknown; readonly completedAtIso: string },
  ): Promise<void> {
    validateIdentity(input.scope, input.key, input.fingerprint);
    let encoded: string | undefined;
    try {
      encoded = JSON.stringify(input.response);
    } catch {
      throw new DomainError("idempotency.unserializable_response", "Idempotency response must be JSON serializable");
    }
    invariant(encoded !== undefined, "idempotency.unserializable_response", "Idempotency response must be JSON serializable");

    await this.database.transaction(async (client) => {
      const completed = await client.query<IdempotencyRow>(
        "idempotency.complete",
        IDEMPOTENCY_COMPLETE_SQL,
        [input.scope.trim(), input.key.trim(), input.fingerprint, encoded, input.completedAtIso],
      );
      if (completed.rowCount === 1) return;

      const current = await client.query<IdempotencyRow>("idempotency.get", IDEMPOTENCY_GET_SQL, [input.scope.trim(), input.key.trim()]);
      invariant(current.rowCount === 1 && current.rows[0], "idempotency.unknown", "Idempotency record does not exist");
      const classified = classifyIdempotencyRow(current.rows[0], input.fingerprint);
      invariant(classified.outcome === "replay", "idempotency.not_processing", "Idempotency record is not available for completion");
    });
  }

  public async abort(scope: string, key: string, fingerprint: string): Promise<void> {
    validateIdentity(scope, key, fingerprint);
    await this.database.query("idempotency.abort", IDEMPOTENCY_ABORT_SQL, [scope.trim(), key.trim(), fingerprint]);
  }
}

function classifyIdempotencyRow(row: IdempotencyRow, fingerprint: string): IdempotencyClaim {
  if (row.fingerprint !== fingerprint) {
    throw new DomainError("idempotency.fingerprint_conflict", "Idempotency key was reused with a different request fingerprint");
  }
  if (row.state === "processing") return { outcome: "in_progress" };
  return { outcome: "replay", response: decodeJson(row.response) };
}

function decodeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    throw new DomainError("idempotency.corrupt_response", "Stored idempotency response is not valid JSON");
  }
}

function validateIdempotencyInput(input: IdempotencyClaimInput): void {
  validateIdentity(input.scope, input.key, input.fingerprint);
  const now = Date.parse(input.nowIso);
  const expiresAt = Date.parse(input.expiresAtIso);
  invariant(Number.isFinite(now) && Number.isFinite(expiresAt), "idempotency.invalid_expiry", "Idempotency timestamps must be valid ISO dates");
  invariant(expiresAt > now, "idempotency.invalid_expiry", "Idempotency expiry must be later than claim time");
}

function validateIdentity(scope: string, key: string, fingerprint: string): void {
  invariant(scope.trim().length >= 1 && scope.trim().length <= 100, "idempotency.invalid_scope", "Idempotency scope must be 1-100 characters");
  invariant(key.trim().length >= 8 && key.trim().length <= 200, "idempotency.invalid_key", "Idempotency key must be 8-200 characters");
  invariant(/^[a-f\d]{64}$/i.test(fingerprint), "idempotency.invalid_fingerprint", "Idempotency fingerprint must be a SHA-256 hex digest");
}

const TELEGRAM_CLAIM_SQL = `
INSERT INTO platform_telegram_update_claims (bot_id, update_id, claimed_at, expires_at)
VALUES ($1, $2, $3::timestamptz, $4::timestamptz)
ON CONFLICT (bot_id, update_id) DO UPDATE
SET claimed_at = EXCLUDED.claimed_at,
    expires_at = EXCLUDED.expires_at
WHERE platform_telegram_update_claims.expires_at <= EXCLUDED.claimed_at
RETURNING update_id`;

export class PostgresTelegramUpdateClaimStore {
  public constructor(private readonly database: SqlDatabase) {}

  public async claim(botId: string, updateId: number, nowIso: string, expiresAtIso: string): Promise<boolean> {
    invariant(botId.trim().length >= 1 && botId.trim().length <= 100, "telegram.invalid_bot_id", "Telegram bot ID must be 1-100 characters");
    invariant(Number.isSafeInteger(updateId) && updateId >= 0, "telegram.invalid_update_id", "Telegram update_id must be a non-negative safe integer");
    invariant(Date.parse(expiresAtIso) > Date.parse(nowIso), "telegram.invalid_claim_expiry", "Telegram claim expiry must be later than claim time");
    const result = await this.database.query<{ readonly update_id: number }>(
      "telegram.claim_update",
      TELEGRAM_CLAIM_SQL,
      [botId.trim(), updateId, nowIso, expiresAtIso],
    );
    return result.rowCount === 1;
  }
}

interface InventoryRow {
  readonly sku: string;
  readonly on_hand: number | string | bigint;
  readonly reserved: number | string | bigint;
}

interface ReservationInventoryRow extends InventoryRow {
  readonly quantity: number | string | bigint;
}

interface ReservationRow {
  readonly fingerprint: string;
  readonly status: "active" | "released" | "sold";
}

const INVENTORY_CREATE_RESERVATION_SQL = `
INSERT INTO platform_inventory_reservations (reservation_id, fingerprint, status, created_at, updated_at)
VALUES ($1, $2, 'active', $3::timestamptz, $3::timestamptz)
ON CONFLICT (reservation_id) DO NOTHING
RETURNING reservation_id`;

const INVENTORY_GET_RESERVATION_SQL = `
SELECT fingerprint, status
FROM platform_inventory_reservations
WHERE reservation_id = $1
FOR UPDATE`;

const INVENTORY_LOCK_STOCK_SQL = `
SELECT sku, on_hand, reserved
FROM platform_inventory_items
WHERE sku = ANY($1::text[])
ORDER BY sku
FOR UPDATE`;

const INVENTORY_INCREMENT_RESERVED_SQL = `
UPDATE platform_inventory_items AS inventory
SET reserved = inventory.reserved + requested.quantity,
    version = inventory.version + 1,
    updated_at = $3::timestamptz
FROM unnest($1::text[], $2::bigint[]) AS requested(sku, quantity)
WHERE inventory.sku = requested.sku`;

const INVENTORY_INSERT_RESERVATION_ITEMS_SQL = `
INSERT INTO platform_inventory_reservation_items (reservation_id, sku, quantity)
SELECT $1, requested.sku, requested.quantity
FROM unnest($2::text[], $3::bigint[]) AS requested(sku, quantity)`;

const INVENTORY_LOCK_RESERVATION_ITEMS_SQL = `
SELECT inventory.sku, inventory.on_hand, inventory.reserved, item.quantity
FROM platform_inventory_reservation_items AS item
JOIN platform_inventory_items AS inventory ON inventory.sku = item.sku
WHERE item.reservation_id = $1
ORDER BY inventory.sku
FOR UPDATE OF inventory`;

const INVENTORY_DECREMENT_RESERVED_SQL = `
UPDATE platform_inventory_items AS inventory
SET reserved = inventory.reserved - item.quantity,
    version = inventory.version + 1,
    updated_at = $2::timestamptz
FROM platform_inventory_reservation_items AS item
WHERE item.reservation_id = $1
  AND inventory.sku = item.sku`;

const INVENTORY_COMMIT_SALE_SQL = `
UPDATE platform_inventory_items AS inventory
SET on_hand = inventory.on_hand - item.quantity,
    reserved = inventory.reserved - item.quantity,
    version = inventory.version + 1,
    updated_at = $2::timestamptz
FROM platform_inventory_reservation_items AS item
WHERE item.reservation_id = $1
  AND inventory.sku = item.sku`;

const INVENTORY_SET_RESERVATION_STATUS_SQL = `
UPDATE platform_inventory_reservations
SET status = $2,
    updated_at = $3::timestamptz
WHERE reservation_id = $1 AND status = 'active'
RETURNING reservation_id`;

export class PostgresInventoryRepository {
  public constructor(private readonly database: SqlDatabase) {}

  public async reserve(reservationId: string, fingerprint: string, requested: readonly StockItem[], nowIso: string): Promise<"created" | "replayed"> {
    validateReservationIdentity(reservationId, fingerprint);
    const items = normalizeStockItems(requested);

    return this.database.transaction(async (client) => {
      const created = await client.query<{ readonly reservation_id: string }>(
        "inventory.create_reservation",
        INVENTORY_CREATE_RESERVATION_SQL,
        [reservationId.trim(), fingerprint, nowIso],
      );
      if (created.rowCount === 0) {
        const current = await getReservation(client, reservationId);
        if (current.fingerprint !== fingerprint) {
          throw new DomainError("inventory.idempotency_conflict", "Reservation ID was reused with different contents");
        }
        invariant(current.status === "active", "inventory.reservation_terminal", "Reservation has already reached a terminal state", { status: current.status });
        return "replayed";
      }

      const skus = items.map((item) => item.sku);
      const quantities = items.map((item) => BigInt(item.quantity));
      const locked = await client.query<InventoryRow>("inventory.lock_stock", INVENTORY_LOCK_STOCK_SQL, [skus]);
      const bySku = new Map(locked.rows.map((row) => [row.sku, row]));
      for (const item of items) {
        const row = bySku.get(item.sku);
        invariant(row, "inventory.unknown_sku", "Inventory item does not exist", { sku: item.sku });
        const available = toBigInt(row.on_hand) - toBigInt(row.reserved);
        invariant(available >= BigInt(item.quantity), "inventory.insufficient_stock", "Not enough available stock", {
          sku: item.sku,
          requested: item.quantity,
          available: available.toString(),
        });
      }

      await client.query("inventory.increment_reserved", INVENTORY_INCREMENT_RESERVED_SQL, [skus, quantities, nowIso]);
      await client.query("inventory.insert_reservation_items", INVENTORY_INSERT_RESERVATION_ITEMS_SQL, [reservationId.trim(), skus, quantities]);
      return "created";
    });
  }

  public async release(reservationId: string, nowIso: string): Promise<"released" | "replayed"> {
    const result = await this.finish(reservationId, nowIso, "released");
    return result === "replayed" ? result : "released";
  }

  public async commitSale(reservationId: string, nowIso: string): Promise<"sold" | "replayed"> {
    const result = await this.finish(reservationId, nowIso, "sold");
    return result === "replayed" ? result : "sold";
  }

  private async finish(reservationId: string, nowIso: string, target: "released" | "sold"): Promise<"released" | "sold" | "replayed"> {
    invariant(reservationId.trim().length >= 1 && reservationId.trim().length <= 200, "inventory.invalid_reservation_id", "Reservation ID must be 1-200 characters");
    return this.database.transaction(async (client) => {
      const current = await getReservation(client, reservationId);
      if (current.status === target) return "replayed";
      invariant(current.status === "active", `inventory.cannot_${target}`, `Only an active reservation can be ${target}` , { status: current.status });

      const locked = await client.query<ReservationInventoryRow>("inventory.lock_reservation_items", INVENTORY_LOCK_RESERVATION_ITEMS_SQL, [reservationId.trim()]);
      invariant(locked.rowCount > 0, "inventory.empty_reservation", "Reservation contains no inventory items");
      for (const row of locked.rows) {
        const quantity = toBigInt(row.quantity);
        invariant(quantity > 0n, "inventory.corrupt_reservation_quantity", "Reservation quantity must be positive", { sku: row.sku });
        invariant(toBigInt(row.reserved) >= quantity, "inventory.corrupt_reserved", "Reserved stock is lower than reservation quantity", {
          sku: row.sku,
          reserved: String(row.reserved),
          quantity: quantity.toString(),
        });
        if (target === "sold") {
          invariant(toBigInt(row.on_hand) >= quantity, "inventory.corrupt_stock", "On-hand stock is lower than reservation quantity", {
            sku: row.sku,
            onHand: String(row.on_hand),
            quantity: quantity.toString(),
          });
        }
      }

      await client.query(
        target === "sold" ? "inventory.commit_sale" : "inventory.decrement_reserved",
        target === "sold" ? INVENTORY_COMMIT_SALE_SQL : INVENTORY_DECREMENT_RESERVED_SQL,
        [reservationId.trim(), nowIso],
      );
      const updated = await client.query<{ readonly reservation_id: string }>(
        "inventory.set_reservation_status",
        INVENTORY_SET_RESERVATION_STATUS_SQL,
        [reservationId.trim(), target, nowIso],
      );
      invariant(updated.rowCount === 1, "inventory.concurrent_transition", "Reservation status changed concurrently");
      return target;
    });
  }
}

async function getReservation(client: SqlClient, reservationId: string): Promise<ReservationRow> {
  const current = await client.query<ReservationRow>("inventory.get_reservation", INVENTORY_GET_RESERVATION_SQL, [reservationId.trim()]);
  invariant(current.rowCount === 1 && current.rows[0], "inventory.unknown_reservation", "Reservation does not exist", { reservationId });
  return current.rows[0];
}

function validateReservationIdentity(reservationId: string, fingerprint: string): void {
  invariant(reservationId.trim().length >= 1 && reservationId.trim().length <= 200, "inventory.invalid_reservation_id", "Reservation ID must be 1-200 characters");
  invariant(/^[a-f\d]{64}$/i.test(fingerprint), "inventory.invalid_fingerprint", "Reservation fingerprint must be a SHA-256 hex digest");
}

function normalizeStockItems(items: readonly StockItem[]): readonly StockItem[] {
  invariant(items.length > 0 && items.length <= 500, "inventory.invalid_item_count", "Reservation must contain 1-500 item rows");
  const totals = new Map<string, number>();
  for (const item of items) {
    const sku = item.sku.trim();
    invariant(sku.length >= 1 && sku.length <= 200, "inventory.invalid_sku", "SKU must be 1-200 characters");
    invariant(Number.isSafeInteger(item.quantity) && item.quantity > 0, "inventory.invalid_quantity", "Inventory quantity must be a positive safe integer", { sku, quantity: item.quantity });
    const total = (totals.get(sku) ?? 0) + item.quantity;
    invariant(Number.isSafeInteger(total), "inventory.quantity_overflow", "Aggregated inventory quantity exceeds safe integer range", { sku });
    totals.set(sku, total);
  }
  return [...totals.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sku, quantity]) => ({ sku, quantity }));
}

function toBigInt(value: number | string | bigint): bigint {
  try {
    return BigInt(value);
  } catch {
    throw new DomainError("persistence.invalid_integer", "Database returned a non-integer numeric value", { value: String(value) });
  }
}
