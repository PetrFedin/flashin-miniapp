import assert from "node:assert/strict";
import test from "node:test";
import { DomainError } from "./errors.js";
import { canonicalJson, PostgresOutboxRepository } from "./outbox.js";
import type { SqlClient, SqlDatabase, SqlResult, SqlValue } from "./persistence.js";

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
    assert.equal(this.cursor, this.steps.length);
  }
}

const nowIso = "2026-07-31T12:00:00.000Z";

function input() {
  return {
    eventId: "telegram:42:reply",
    topic: "telegram.sendMessage",
    partitionKey: "chat:1001",
    payload: { text: "Order created", chatId: 1001 },
    availableAtIso: nowIso,
    nowIso,
  };
}

test("canonical outbox JSON is independent of object key order", () => {
  assert.equal(canonicalJson({ b: 2, a: { d: 4, c: 3 } }), canonicalJson({ a: { c: 3, d: 4 }, b: 2 }));
});

test("outbox enqueue creates one durable event", async () => {
  const database = new ScriptedDatabase([
    {
      name: "outbox.enqueue",
      rows: [{ event_id: input().eventId }],
      inspect: (values) => {
        assert.equal(values[0], input().eventId);
        assert.equal(values[1], input().topic);
        assert.equal(values[2], input().partitionKey);
        assert.equal(typeof values[3], "string");
        assert.equal(values[4], canonicalJson(input().payload));
      },
    },
  ]);
  assert.equal(await new PostgresOutboxRepository(database).enqueue(input()), "created");
  assert.equal(database.transactions, 1);
  database.assertDone();
});

test("outbox enqueue replays identical event ID and rejects conflicting contents", async () => {
  const payload = input();
  const fingerprintDatabase = new ScriptedDatabase([
    { name: "outbox.enqueue", rowCount: 0 },
    {
      name: "outbox.get",
      rows: [{ fingerprint: "f".repeat(64), status: "pending" }],
    },
  ]);
  await assert.rejects(
    () => new PostgresOutboxRepository(fingerprintDatabase).enqueue(payload),
    (error: unknown) => error instanceof DomainError && error.code === "outbox.idempotency_conflict",
  );
  fingerprintDatabase.assertDone();
});

test("outbox worker claims in a lease, marks sent, and refuses a lost lease", async () => {
  const database = new ScriptedDatabase([
    {
      name: "outbox.claim_batch",
      rows: [{ event_id: "e-1", topic: "telegram.sendMessage", partition_key: "chat:1", payload: '{"chatId":1}', attempts: "2" }],
    },
    { name: "outbox.mark_sent", rows: [{ event_id: "e-1" }] },
    { name: "outbox.mark_sent", rowCount: 0 },
  ]);
  const repository = new PostgresOutboxRepository(database);
  const messages = await repository.claimBatch({
    nowIso,
    lockedUntilIso: "2026-07-31T12:01:00.000Z",
    workerId: "telegram-worker-1",
    limit: 10,
  });
  assert.deepEqual(messages, [{ eventId: "e-1", topic: "telegram.sendMessage", partitionKey: "chat:1", payload: { chatId: 1 }, attempts: 2 }]);
  await repository.markSent({ eventId: "e-1", workerId: "telegram-worker-1", nowIso, sentAtIso: nowIso });
  await assert.rejects(
    () => repository.markSent({ eventId: "e-1", workerId: "telegram-worker-2", nowIso, sentAtIso: nowIso }),
    (error: unknown) => error instanceof DomainError && error.code === "outbox.lost_lease",
  );
  database.assertDone();
});

test("outbox payload rejects circular and non-JSON values", () => {
  const circular: Record<string, unknown> = {};
  circular.self = circular;
  assert.throws(
    () => canonicalJson(circular),
    (error: unknown) => error instanceof DomainError && error.code === "outbox.circular_payload",
  );
  assert.throws(
    () => canonicalJson({ amount: 1n }),
    (error: unknown) => error instanceof DomainError && error.code === "outbox.unserializable_payload",
  );
});
