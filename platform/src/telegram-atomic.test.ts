import assert from "node:assert/strict";
import test from "node:test";
import { initialBotSession } from "./bot-flow.js";
import { DomainError } from "./errors.js";
import type { SqlClient, SqlDatabase, SqlResult, SqlValue } from "./persistence.js";
import { PostgresTelegramAtomicProcessor } from "./telegram-atomic.js";

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

function atomicInput() {
  return {
    botId: "flashin-bot",
    updateId: 42,
    chatId: 1001,
    userId: 1001,
    action: { type: "open_catalog" as const },
    nowIso,
    updateExpiresAtIso: "2026-08-01T12:00:00.000Z",
    sessionExpiresAtIso: "2026-08-07T12:00:00.000Z",
    outbox: {
      eventId: "telegram:update:42:reply",
      topic: "telegram.sendMessage",
      partitionKey: "chat:1001",
      payload: { chatId: 1001, text: "Catalog" },
      availableAtIso: nowIso,
      nowIso,
    },
  };
}

test("Telegram update claim, session mutation and response outbox share one transaction", async () => {
  const initial = initialBotSession();
  const database = new ScriptedDatabase([
    { name: "telegram_atomic.claim_update", rows: [{ update_id: 42 }] },
    { name: "telegram_atomic.ensure_session", rows: [{ version: 0 }] },
    { name: "telegram_atomic.lock_session", rows: [{ user_id: 1001, version: 0, session: initial }] },
    {
      name: "telegram_atomic.update_session",
      rows: [{ version: 1 }],
      inspect: (values) => {
        assert.equal(values[3], 1);
        assert.deepEqual(JSON.parse(String(values[4])), { version: 1, scene: { kind: "catalog", page: 1 }, cart: [] });
        assert.equal(values[7], 0);
      },
    },
    { name: "outbox.enqueue", rows: [{ event_id: "telegram:update:42:reply" }] },
  ]);

  const result = await new PostgresTelegramAtomicProcessor(database).process(atomicInput());
  assert.deepEqual(result, {
    outcome: "processed",
    session: { version: 1, scene: { kind: "catalog", page: 1 }, cart: [] },
    outbox: "created",
  });
  assert.equal(database.transactions, 1);
  database.assertDone();
});

test("duplicate Telegram update exits before session or outbox mutation", async () => {
  const database = new ScriptedDatabase([{ name: "telegram_atomic.claim_update", rowCount: 0 }]);
  assert.deepEqual(await new PostgresTelegramAtomicProcessor(database).process(atomicInput()), { outcome: "duplicate" });
  assert.equal(database.transactions, 1);
  database.assertDone();
});

test("atomic Telegram processing rejects a session owned by another user", async () => {
  const database = new ScriptedDatabase([
    { name: "telegram_atomic.claim_update", rows: [{ update_id: 42 }] },
    { name: "telegram_atomic.ensure_session", rowCount: 0 },
    { name: "telegram_atomic.lock_session", rows: [{ user_id: 2002, version: 0, session: initialBotSession() }] },
  ]);
  await assert.rejects(
    () => new PostgresTelegramAtomicProcessor(database).process(atomicInput()),
    (error: unknown) => error instanceof DomainError && error.code === "telegram_atomic.user_mismatch",
  );
  database.assertDone();
});
