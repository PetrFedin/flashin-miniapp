import { DomainError, invariant } from "./errors.js";

export interface StockItem {
  readonly sku: string;
  readonly quantity: number;
}

export interface StockState {
  readonly onHand: number;
  readonly reserved: number;
  readonly available: number;
}

type ReservationStatus = "active" | "released" | "sold";

interface Reservation {
  readonly fingerprint: string;
  readonly items: readonly StockItem[];
  status: ReservationStatus;
}

function normalizeItems(items: readonly StockItem[]): readonly StockItem[] {
  const aggregated = new Map<string, number>();
  for (const item of items) {
    const sku = item.sku.trim();
    invariant(sku.length > 0, "inventory.empty_sku", "SKU cannot be empty");
    invariant(
      Number.isSafeInteger(item.quantity) && item.quantity > 0,
      "inventory.invalid_quantity",
      "Inventory quantity must be a positive safe integer",
      { sku, quantity: item.quantity },
    );
    aggregated.set(sku, (aggregated.get(sku) ?? 0) + item.quantity);
  }
  return [...aggregated.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sku, quantity]) => ({ sku, quantity }));
}

function fingerprint(items: readonly StockItem[]): string {
  return items.map((item) => `${item.sku}:${item.quantity}`).join("|");
}

export class InventoryBook {
  private readonly stock = new Map<string, { onHand: number; reserved: number }>();
  private readonly reservations = new Map<string, Reservation>();

  public setStock(sku: string, onHand: number): void {
    const normalized = sku.trim();
    invariant(normalized.length > 0, "inventory.empty_sku", "SKU cannot be empty");
    invariant(Number.isSafeInteger(onHand) && onHand >= 0, "inventory.invalid_stock", "Stock must be a non-negative safe integer");
    const current = this.stock.get(normalized) ?? { onHand: 0, reserved: 0 };
    invariant(onHand >= current.reserved, "inventory.stock_below_reserved", "Stock cannot be lower than active reservations", {
      sku: normalized,
      onHand,
      reserved: current.reserved,
    });
    current.onHand = onHand;
    this.stock.set(normalized, current);
  }

  public reserve(reservationId: string, requested: readonly StockItem[]): void {
    const normalizedId = reservationId.trim();
    invariant(normalizedId.length > 0, "inventory.empty_reservation", "Reservation ID cannot be empty");
    const items = normalizeItems(requested);
    const requestedFingerprint = fingerprint(items);
    const existing = this.reservations.get(normalizedId);

    if (existing) {
      if (existing.fingerprint !== requestedFingerprint) {
        throw new DomainError("inventory.idempotency_conflict", "Reservation ID was reused with different items", {
          reservationId: normalizedId,
        });
      }
      if (existing.status === "active") return;
      throw new DomainError("inventory.reservation_terminal", "Reservation has already reached a terminal state", {
        reservationId: normalizedId,
        status: existing.status,
      });
    }

    for (const item of items) {
      const state = this.stock.get(item.sku) ?? { onHand: 0, reserved: 0 };
      invariant(
        state.onHand - state.reserved >= item.quantity,
        "inventory.insufficient_stock",
        "Not enough available stock",
        { sku: item.sku, requested: item.quantity, available: state.onHand - state.reserved },
      );
    }

    for (const item of items) {
      const state = this.stock.get(item.sku)!;
      state.reserved += item.quantity;
    }
    this.reservations.set(normalizedId, { fingerprint: requestedFingerprint, items, status: "active" });
  }

  public release(reservationId: string): void {
    const reservation = this.getReservation(reservationId);
    if (reservation.status === "released") return;
    invariant(reservation.status === "active", "inventory.cannot_release", "Only active reservations can be released");
    for (const item of reservation.items) {
      const state = this.stock.get(item.sku)!;
      state.reserved -= item.quantity;
    }
    reservation.status = "released";
  }

  public commitSale(reservationId: string): void {
    const reservation = this.getReservation(reservationId);
    if (reservation.status === "sold") return;
    invariant(reservation.status === "active", "inventory.cannot_sell", "Only active reservations can be committed as a sale");
    for (const item of reservation.items) {
      const state = this.stock.get(item.sku)!;
      invariant(state.reserved >= item.quantity, "inventory.corrupt_reservation", "Reserved stock is lower than reservation quantity");
      invariant(state.onHand >= item.quantity, "inventory.corrupt_stock", "On-hand stock is lower than reservation quantity");
      state.reserved -= item.quantity;
      state.onHand -= item.quantity;
    }
    reservation.status = "sold";
  }

  public getStock(sku: string): StockState {
    const state = this.stock.get(sku.trim()) ?? { onHand: 0, reserved: 0 };
    return { onHand: state.onHand, reserved: state.reserved, available: state.onHand - state.reserved };
  }

  private getReservation(reservationId: string): Reservation {
    const reservation = this.reservations.get(reservationId.trim());
    invariant(reservation, "inventory.unknown_reservation", "Reservation does not exist", { reservationId });
    return reservation;
  }
}

