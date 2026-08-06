import { invariant } from "./errors.js";
import { decodeBotSession } from "./bot-session.js";
import { initialBotSession, reduceBotSession } from "./bot-flow.js";
import type { BotAction, BotSession } from "./bot-types.js";
import { PostgresOutboxRepository, type OutboxEnqueueInput } from "./outbox.js";
import type { SqlClient, SqlDatabase } from "./persistence.js";

export interface AtomicTelegramUpdateInput {
  readonly botId: string;
  readonly updateId: number;
  readonly chatId: number;
  readonly userId: number;
  readonly action: BotAction;
  readonly nowIso: string;
  readonly updateExpiresAtIso: string;
  readonly sessionExpiresAtIso: string;
  readonly outbox: OutboxEnqueueInput;
}

export type AtomicTelegramUpdateResult =
  | { readonly outcome: "duplicate" }
  | { readonly outcome: "processed"; readonly session: BotSession; readonly outbox: "created" | "replayed" };

interface SessionRow {
  readonly user_id: number | string | bigint;
  readonly version: number | string | bigint;
  readonly session: unknown;
}

const CLAIM_UPDATE_SQL = `
INSERT INTO platform_telegram_update_claims (bot_id, update_id, claimed_at, expires_at)
VALUES ($1, $2, $3::timestamptz, $4::timestamptz)
ON CONFLICT (bot_id, update_id) DO UPDATE
SET claimed_at = EXCLUDED.claimed_at,
    expires_at = EXCLUDED.expires_at
WHERE platform_telegram_update_claims.expires_at <= EXCLUDED.claimed_at
RETURNING update_id`;

const ENSURE_SESSION_SQL = `
INSERT INTO platform_bot_sessions (
  bot_id, chat_id, user_id, version, session, created_at, updated_at, expires_at
)
VALUES ($1, $2, $3, 0, $4::jsonb, $5::timestamptz, $5::timestamptz, $6::timestamptz)
ON CONFLICT (bot_id, chat_id) DO UPDATE
SET user_id = EXCLUDED.user_id,
    version = 0,
    session = EXCLUDED.session,
    updated_at = EXCLUDED.updated_at,
    expires_at = EXCLUDED.expires_at
WHERE platform_bot_sessions.expires_at <= EXCLUDED.updated_at
RETURNING version`;

const LOCK_SESSION_SQL = `
SELECT user_id, version, session
FROM platform_bot_sessions
WHERE bot_id = $1 AND chat_id = $2
FOR UPDATE`;

const UPDATE_SESSION_SQL = `
UPDATE platform_bot_sessions
SET version = $4,
    session = $5::jsonb,
    updated_at = $6::timestamptz,
    expires_at = $7::timestamptz
WHERE bot_id = $1
  AND chat_id = $2
  AND user_id = $3
  AND version = $8
RETURNING version`;

export class PostgresTelegramAtomicProcessor {
  private readonly outbox: PostgresOutboxRepository;

  public constructor(private readonly database: SqlDatabase) {
    this.outbox = new PostgresOutboxRepository(database);
  }

  public async process(input: AtomicTelegramUpdateInput): Promise<AtomicTelegramUpdateResult> {
    validateInput(input);
    return this.database.transaction(async (client) => {
      const claimed = await client.query<{ readonly update_id: number | string | bigint }>(
        "telegram_atomic.claim_update",
        CLAIM_UPDATE_SQL,
        [input.botId.trim(), input.updateId, input.nowIso, input.updateExpiresAtIso],
      );
      if (claimed.rowCount === 0) return { outcome: "duplicate" };

      const initial = initialBotSession();
      await client.query<{ readonly version: number | string | bigint }>(
        "telegram_atomic.ensure_session",
        ENSURE_SESSION_SQL,
        [
          input.botId.trim(),
          input.chatId,
          input.userId,
          JSON.stringify(initial),
          input.nowIso,
          input.sessionExpiresAtIso,
        ],
      );

      const current = await lockSession(client, input.botId, input.chatId);
      invariant(toSafeInteger(current.user_id, "telegram_atomic.invalid_user_id") === input.userId, "telegram_atomic.user_mismatch", "Telegram chat session belongs to a different user", {
        chatId: input.chatId,
        expectedUserId: input.userId,
        actualUserId: String(current.user_id),
      });
      const rowVersion = toSafeInteger(current.version, "telegram_atomic.invalid_version");
      const session = decodeBotSession(current.session);
      invariant(session.version === rowVersion, "telegram_atomic.version_mismatch", "Telegram session JSON version does not match the relational version column", {
        jsonVersion: session.version,
        rowVersion,
      });

      const next = reduceBotSession(session, input.action);
      const updated = await client.query<{ readonly version: number | string | bigint }>(
        "telegram_atomic.update_session",
        UPDATE_SESSION_SQL,
        [
          input.botId.trim(),
          input.chatId,
          input.userId,
          next.version,
          JSON.stringify(next),
          input.nowIso,
          input.sessionExpiresAtIso,
          rowVersion,
        ],
      );
      invariant(updated.rowCount === 1, "telegram_atomic.concurrent_session_update", "Telegram session changed before the atomic update could be committed");

      const outboxOutcome = await this.outbox.enqueueWithClient(client, input.outbox);
      return { outcome: "processed", session: next, outbox: outboxOutcome };
    });
  }
}

async function lockSession(client: SqlClient, botId: string, chatId: number): Promise<SessionRow> {
  const locked = await client.query<SessionRow>("telegram_atomic.lock_session", LOCK_SESSION_SQL, [botId.trim(), chatId]);
  invariant(locked.rowCount === 1 && locked.rows[0], "telegram_atomic.session_missing", "Telegram session disappeared while acquiring its row lock");
  return locked.rows[0];
}

function validateInput(input: AtomicTelegramUpdateInput): void {
  const botId = input.botId.trim();
  invariant(botId.length >= 1 && botId.length <= 100, "telegram_atomic.invalid_bot_id", "Bot ID must be 1-100 characters");
  invariant(Number.isSafeInteger(input.updateId) && input.updateId >= 0, "telegram_atomic.invalid_update_id", "Telegram update ID must be a non-negative safe integer");
  invariant(Number.isSafeInteger(input.chatId) && input.chatId > 0, "telegram_atomic.invalid_chat_id", "Telegram chat ID must be a positive safe integer");
  invariant(Number.isSafeInteger(input.userId) && input.userId > 0, "telegram_atomic.invalid_user_id", "Telegram user ID must be a positive safe integer");
  validateFuture(input.nowIso, input.updateExpiresAtIso, "telegram_atomic.invalid_update_expiry");
  validateFuture(input.nowIso, input.sessionExpiresAtIso, "telegram_atomic.invalid_session_expiry");
  invariant(input.outbox.nowIso === input.nowIso, "telegram_atomic.outbox_clock_mismatch", "Outbox and Telegram transaction timestamps must match exactly");
}

function validateFuture(nowIso: string, futureIso: string, code: string): void {
  const now = Date.parse(nowIso);
  const future = Date.parse(futureIso);
  invariant(Number.isFinite(now) && Number.isFinite(future) && future > now, code, "Expiry must be a valid timestamp later than the transaction timestamp");
}

function toSafeInteger(value: number | string | bigint, code: string): number {
  const parsed = Number(value);
  invariant(Number.isSafeInteger(parsed) && parsed >= 0, code, "Database returned an invalid non-negative safe integer", { value: String(value) });
  return parsed;
}
