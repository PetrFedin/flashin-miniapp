declare module "node:crypto" {
  interface Hash {
    update(data: string, encoding?: string): Hash;
    digest(encoding: "hex"): string;
  }
  interface Hmac {
    update(data: string, encoding?: string): Hmac;
    digest(): Uint8Array;
    digest(encoding: "hex"): string;
  }
  export function createHash(algorithm: string): Hash;
  export function createHmac(algorithm: string, key: string | Uint8Array): Hmac;
  export function timingSafeEqual(left: Uint8Array, right: Uint8Array): boolean;
}

declare module "node:assert/strict" {
  const assert: {
    equal(actual: unknown, expected: unknown, message?: string): void;
    deepEqual(actual: unknown, expected: unknown): void;
    match(actual: string, expected: RegExp): void;
    throws(block: () => unknown, validator?: (error: unknown) => boolean): void;
    doesNotThrow(block: () => unknown): void;
    ok(value: unknown, message?: string): asserts value;
    rejects(block: () => Promise<unknown>, validator?: (error: unknown) => boolean): Promise<void>;
  };
  export default assert;
}

declare module "node:test" {
  const test: (name: string, block: () => unknown | Promise<unknown>) => void;
  export default test;
}

declare const Buffer: {
  from(value: string, encoding?: string): Uint8Array;
};
