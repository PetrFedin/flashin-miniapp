import { invariant } from "./errors.js";

const MONEY_PATTERN = /^(-?)(0|[1-9]\d*)(?:\.(\d{1,2}))?$/;

export class Money {
  public static readonly zero = new Money(0n);

  private constructor(public readonly minor: bigint) {}

  public static fromMinor(minor: bigint | number): Money {
    if (typeof minor === "number") {
      invariant(Number.isSafeInteger(minor), "money.unsafe_minor", "Minor units must be a safe integer");
      return new Money(BigInt(minor));
    }
    return new Money(minor);
  }

  public static parse(value: string): Money {
    const normalized = value.trim();
    const match = MONEY_PATTERN.exec(normalized);
    invariant(match, "money.invalid", "Money must be a base-10 value with at most two decimals", { value });

    const sign = match[1] === "-" ? -1n : 1n;
    const major = BigInt(match[2]!);
    const fractional = (match[3] ?? "").padEnd(2, "0");
    return new Money(sign * (major * 100n + BigInt(fractional || "0")));
  }

  public add(other: Money): Money {
    return new Money(this.minor + other.minor);
  }

  public subtract(other: Money): Money {
    return new Money(this.minor - other.minor);
  }

  public multiply(quantity: number): Money {
    invariant(Number.isSafeInteger(quantity), "money.invalid_quantity", "Quantity must be a safe integer", { quantity });
    return new Money(this.minor * BigInt(quantity));
  }

  public percentage(basisPoints: number): Money {
    invariant(
      Number.isSafeInteger(basisPoints) && basisPoints >= 0 && basisPoints <= 10_000,
      "money.invalid_basis_points",
      "Basis points must be an integer between 0 and 10000",
      { basisPoints },
    );
    const numerator = this.minor * BigInt(basisPoints);
    const adjustment = numerator >= 0n ? 5_000n : -5_000n;
    return new Money((numerator + adjustment) / 10_000n);
  }

  public min(other: Money): Money {
    return this.minor <= other.minor ? this : other;
  }

  public isNegative(): boolean {
    return this.minor < 0n;
  }

  public equals(other: Money): boolean {
    return this.minor === other.minor;
  }

  public toJSON(): string {
    return this.toString();
  }

  public toString(): string {
    const sign = this.minor < 0n ? "-" : "";
    const absolute = this.minor < 0n ? -this.minor : this.minor;
    return `${sign}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, "0")}`;
  }
}

