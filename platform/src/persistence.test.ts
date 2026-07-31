import assert from "node:assert/strict";
import test from "node:test";
import {
  DomainError,
  PostgresIdempotencyRepository,
  PostgresInventoryRepository,
  PostgresTelegramUpdateClaimStore,
  type SqlClient,
  type SqlDatabase,
  type SqlResult,
  type SqlValue,
} from "./index.js";

interface Step {
  readonly name: string;
  readonly rows?: readonly unknown[];
  readonly rowCount?: number;
  readonly inspect?: (values: readonly SqlValue[]) => void;
}

class ScriptedDatabase implements SqlDatabase {
  private cursor = 0;
  public transactions = 0;

  public constructor(private readonly steps: readonly Step[]) {}

  public async query<Row>(name: string, _text: string, values: readonly SqlValue[] = []): Promise<SqlResult<Row>> {
    const step = this.steps[this.cursor++];
    assert.ok(step, `Unexpected SQL statement ${name}`);
    assert.equal(name, step.name);
    step.inspect?.(values);
    const rows = (step.rows ?? []) as readonly Row[];
    return { rows, rowCount: step.rowCount ?? rows.length };
  }

  public async transaction<T>(operation: (client: SqlClient) => Promise<T>): Promise<T> {
    this.transactions += 1;
    return operation(this);
  }

  public assertDone(): void {
    assert.equal(this.cursor, this.steps.length, "Not all scripted SQL statements were consumed");
  }
}

const fingerprint = "a".repeat(64);

test("PostgreSQL idempotency claim creates a durable processing record", async () => {
  const db = new ScriptedDatabase([
    {
      name: "idempotency.claim",
      rows: [{ fingerprint, state: "processing", response: null }],
      inspect: (values) => assert.deepEqual(values.slice(0, 3), ["checkout", "request-0001", fingerprint]),
    },
  ]);
  const repository = new PostgresIdempotencyRepository(db);
  const result = await repository.claim({
    scope: "checkout",
    key: "request-0001",
    fingerprint,
    nowIso: "2026-07-31T10:00:00.000Z",
    expiresAtIso: "2026-07-31T11:00:00.000Z",
  });
  assert.deepEqual(result, { outcome: "claimed" });
  assert.equal(db.transactions, 1);
  db.assertDone();
});

test("PostgreSQL idempotency returns stored response for an exact replay", async () => {
  const db = new ScriptedDatabase([
    { name: "idempotency.claim", rowCount: 0 },
    { name: "idempotency.get", rows: [{ fingerprint, state: "completed", response: '{"orderId":"o-1"}' }] },
  ]);
  const result = await new PostgresIdempotencyRepository(db).claim({
    scope: "checkout",
    key: "request-0001",
    fingerprint,
    nowIso: "2026-07-31T10:00:00.000Z",
    expiresAtIso: "2026-07-31T11:00:00.000Z",
  });
  assert.deepEqual(result, { outcome: "replay", response: { orderId: "o-1" } });
  db.assertDone();
});

test("PostgreSQL idempotency rejects a key reused with a different fingerprint", async () => {
  const db = new ScriptedDatabase([
    { name: "idempotency.claim", rowCount: 0 },
    { name: "idempotency.get", rows: [{ fingerprint: "b".repeat(64), state: "processing", response: null }] },
  ]);
  await assert.rejects(
    () =>
      new PostgresIdempotencyRepository(db).claim({
        scope: "checkout",
        key: "request-0001",
        fingerprint,
        nowIso: "2026-07-31T10:00:00.000Z",
        expiresAtIso: "2026-07-31T11:00:00.000Z",
      }),
    (error: unknown) => error instanceof DomainError && error.code === "idempotency.fingerprint_conflict",
  );
  db.assertDone();
});

test("PostgreSQL Telegram update claim is atomic and reusable only after SQL expiry", async () => {
  const acceptedDb = new ScriptedDatabase([{ name: "telegram.claim_update", rows: [{ update_id: 42 }] }]);
  const duplicateDb = new ScriptedDatabase([{ name: "telegram.claim_update", rowCount: 0 }]);
  assert.equal(
    await new PostgresTelegramUpdateClaimStore(acceptedDb).claim(
      "flashin-bot",
      42,
      "2026-07-31T10:00:00.000Z",
      "2026-07-31T11:00:00.000Z",
    ),
    true,
  );
  assert.equal(
    await new PostgresTelegramUpdateClaimStore(duplicateDb).claim(
      "flashin-bot",
      42,
      "2026-07-31T10:00:01.000Z",
      "2026-07-31T11:00:01.000Z",
    ),
    false,
  );
});

test("PostgreSQL inventory reservation locks SKU rows in deterministic order before mutation", async () => {
  const db = new ScriptedDatabase([
    { name: "inventory.create_reservation", rows: [{ reservation_id: "r-1" }] },
    {
      name: "inventory.lock_stock",
      rows: [
        { sku: "A", on_hand: "5", reserved: "1" },
        { sku: "B", on_hand: "2", reserved: "0" },
      ],
      inspect: (values) => assert.deepEqual(values[0], ["A", "B"]),
    },
    {
      name: "inventory.increment_reserved",
      inspect: (values) => {
        assert.deepEqual(values[0], ["A", "B"]);
        assert.deepEqual(values[1], [2n, 1n]);
      },
    },
    { name: "inventory.insert_reservation_items" },
  ]);
  const result = await new PostgresInventoryRepository(db).reserve(
    "r-1",
    fingerprint,
    [
      { sku: "B", quantity: 1 },
      { sku: "A", quantity: 1 },
      { sku: "A", quantity: 1 },
    ],
    "2026-07-31T10:00:00.000Z",
  );
  assert.equal(result, "created");
  assert.equal(db.transactions, 1);
  db.assertDone();
});

test("PostgreSQL inventory reservation fails before any stock mutation when availability is insufficient", async () => {
  const db = new ScriptedDatabase([
    { name: "inventory.create_reservation", rows: [{ reservation_id: "r-1" }] },
    { name: "inventory.lock_stock", rows: [{ sku: "A", on_hand: "1", reserved: "0" }] },
  ]);
  await assert.rejects(
    () => new PostgresInventoryRepository(db).reserve("r-1", fingerprint, [{ sku: "A", quantity: 2 }], "2026-07-31T10:00:00.000Z"),
    (error: unknown) => error instanceof DomainError && error.code === "inventory.insufficient_stock",
  );
  db.assertDone();
});

test("PostgreSQL inventory sale locks reservation and stock before the terminal transition", async () => {
  const db = new ScriptedDatabase([
    { name: "inventory.get_reservation", rows: [{ fingerprint, status: "active" }] },
    { name: "inventory.lock_reservation_items", rows: [{ sku: "A", on_hand: "5", reserved: "2", quantity: "2" }] },
    { name: "inventory.commit_sale" },
    { name: "inventory.set_reservation_status", rows: [{ reservation_id: "r-1" }] },
  ]);
  assert.equal(await new PostgresInventoryRepository(db).commitSale("r-1", "2026-07-31T10:00:00.000Z"), "sold");
  db.assertDone();
});


test("PostgreSQL idempotency completion rejects non-JSON response values with a domain error", async () => {
  const db = new ScriptedDatabase([]);
  await assert.rejects(
    () =>
      new PostgresIdempotencyRepository(db).complete({
        scope: "checkout",
        key: "request-0001",
        fingerprint,
        response: { invalid: 1n },
        completedAtIso: "2026-07-31T10:05:00.000Z",
      }),
    (error: unknown) => error instanceof DomainError && error.code === "idempotency.unserializable_response",
  );
  db.assertDone();
});

test("PostgreSQL inventory sale rejects corrupt reservation quantity before mutation", async () => {
  const db = new ScriptedDatabase([
    { name: "inventory.get_reservation", rows: [{ fingerprint, status: "active" }] },
    { name: "inventory.lock_reservation_items", rows: [{ sku: "A", on_hand: "5", reserved: "1", quantity: "2" }] },
  ]);
  await assert.rejects(
    () => new PostgresInventoryRepository(db).commitSale("r-1", "2026-07-31T10:00:00.000Z"),
    (error: unknown) => error instanceof DomainError && error.code === "inventory.corrupt_reserved",
  );
  db.assertDone();
});
