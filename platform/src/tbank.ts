import { createHash, timingSafeEqual } from "node:crypto";
import { DomainError, invariant } from "./errors.js";
import { Money } from "./money.js";

export type TBankPaymentStatus =
  | "NEW"
  | "FORM_SHOWED"
  | "AUTHORIZING"
  | "AUTHORIZED"
  | "CONFIRMING"
  | "CONFIRMED"
  | "REVERSING"
  | "PARTIAL_REVERSED"
  | "REVERSED"
  | "REFUNDING"
  | "PARTIAL_REFUNDED"
  | "REFUNDED"
  | "CANCELED"
  | "DEADLINE_EXPIRED"
  | "REJECTED";

export type TBankRootValue = string | number | boolean | null | undefined | readonly unknown[] | Readonly<Record<string, unknown>>;
export type TBankPayload = Readonly<Record<string, TBankRootValue>>;

export function createTBankToken(payload: TBankPayload, password: string): string {
  invariant(password.length > 0, "tbank.empty_password", "T-Bank terminal password cannot be empty");
  const values: Record<string, string> = { Password: password };

  for (const [key, value] of Object.entries(payload)) {
    if (key === "Token" || value === null || value === undefined || typeof value === "object") continue;
    values[key] = String(value);
  }

  const source = Object.keys(values)
    .sort((left, right) => left.localeCompare(right))
    .map((key) => values[key])
    .join("");
  return createHash("sha256").update(source, "utf8").digest("hex");
}

export function verifyTBankNotification(payload: TBankPayload, password: string): boolean {
  const provided = payload.Token;
  if (typeof provided !== "string" || !/^[a-f\d]{64}$/i.test(provided)) return false;
  const expected = createTBankToken(payload, password);
  return timingSafeEqual(Buffer.from(provided.toLowerCase(), "hex"), Buffer.from(expected, "hex"));
}

export interface TBankReceiptItem {
  readonly Name: string;
  readonly Price: number;
  readonly Quantity: number;
  readonly Amount: number;
  readonly Tax: string;
}

export interface TBankInitInput {
  readonly terminalKey: string;
  readonly password: string;
  readonly orderId: string;
  readonly amount: Money;
  readonly description?: string;
  readonly notificationUrl?: string;
  readonly items: readonly TBankReceiptItem[];
}

export interface TBankInitRequest {
  readonly TerminalKey: string;
  readonly Amount: number;
  readonly OrderId: string;
  readonly Description?: string;
  readonly NotificationURL?: string;
  readonly Receipt: { readonly Items: readonly TBankReceiptItem[] };
  readonly Token: string;
}

export function buildTBankInitRequest(input: TBankInitInput): TBankInitRequest {
  const terminalKey = input.terminalKey.trim();
  const orderId = input.orderId.trim();
  invariant(terminalKey.length > 0 && terminalKey.length <= 64, "tbank.invalid_terminal", "TerminalKey must be 1-64 characters");
  invariant(orderId.length > 0 && orderId.length <= 50, "tbank.invalid_order_id", "OrderId must be 1-50 characters");
  invariant(input.amount.minor > 0n, "tbank.invalid_amount", "Payment amount must be positive");
  invariant(input.amount.minor <= BigInt(Number.MAX_SAFE_INTEGER), "tbank.amount_too_large", "Payment amount exceeds safe integer range");
  invariant(input.items.length > 0, "tbank.empty_receipt", "Receipt must contain at least one item");

  let receiptAmount = 0;
  for (const item of input.items) {
    invariant(item.Name.trim().length > 0, "tbank.empty_item_name", "Receipt item name cannot be empty");
    invariant(Number.isSafeInteger(item.Price) && item.Price >= 0, "tbank.invalid_item_price", "Receipt item price must be non-negative kopeks");
    invariant(Number.isFinite(item.Quantity) && item.Quantity > 0, "tbank.invalid_item_quantity", "Receipt item quantity must be positive");
    invariant(Number.isSafeInteger(item.Amount) && item.Amount >= 0, "tbank.invalid_item_amount", "Receipt item amount must be non-negative kopeks");
    receiptAmount += item.Amount;
  }

  const amount = Number(input.amount.minor);
  invariant(receiptAmount === amount, "tbank.receipt_mismatch", "Payment amount must equal the sum of receipt item amounts", {
    amount,
    receiptAmount,
  });

  const unsigned = {
    TerminalKey: terminalKey,
    Amount: amount,
    OrderId: orderId,
    ...(input.description ? { Description: input.description } : {}),
    ...(input.notificationUrl ? { NotificationURL: input.notificationUrl } : {}),
    Receipt: { Items: input.items },
  };
  return { ...unsigned, Token: createTBankToken(unsigned, input.password) };
}

export function classifyTBankStatus(status: TBankPaymentStatus):
  | "pending"
  | "authorized"
  | "confirmed"
  | "partially_refunded"
  | "refunded"
  | "cancelled"
  | "failed" {
  if (["NEW", "FORM_SHOWED", "AUTHORIZING", "CONFIRMING", "REVERSING", "REFUNDING"].includes(status)) return "pending";
  if (status === "AUTHORIZED") return "authorized";
  if (status === "CONFIRMED") return "confirmed";
  if (status === "PARTIAL_REFUNDED") return "partially_refunded";
  if (status === "REFUNDED") return "refunded";
  if (["CANCELED", "REVERSED", "PARTIAL_REVERSED"].includes(status)) return "cancelled";
  if (["DEADLINE_EXPIRED", "REJECTED"].includes(status)) return "failed";
  throw new DomainError("tbank.unknown_status", "Unknown T-Bank payment status", { status });
}

