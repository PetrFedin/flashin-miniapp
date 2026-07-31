import { DomainError, invariant } from "./errors.js";
import { Money } from "./money.js";

export interface TildaProductRow {
  readonly identity: string;
  readonly tildaUid?: string;
  readonly externalId?: string;
  readonly parentId?: string;
  readonly sku: string;
  readonly brand?: string;
  readonly category?: string;
  readonly title: string;
  readonly description?: string;
  readonly photo?: string;
  readonly price: Money;
  readonly oldPrice?: Money;
  readonly quantity: number | null;
  readonly editions?: string;
}

export function parseTildaCsv(source: string): readonly TildaProductRow[] {
  const rows = parseSemicolonCsv(source.replace(/^\uFEFF/, ""));
  invariant(rows.length >= 2, "tilda.empty_feed", "Tilda CSV must contain a header and at least one product row");
  const header = rows[0]!.map((column) => column.trim());
  const index = new Map(header.map((column, position) => [column, position]));

  for (const required of ["SKU", "Title", "Price"]) {
    invariant(index.has(required), "tilda.missing_column", `Tilda CSV is missing required column ${required}`, { required });
  }

  const result = new Map<string, TildaProductRow>();
  for (let rowNumber = 1; rowNumber < rows.length; rowNumber += 1) {
    const row = rows[rowNumber]!;
    if (row.every((value) => value.trim() === "")) continue;
    const get = (name: string): string => row[index.get(name) ?? -1]?.trim() ?? "";

    const sku = get("SKU");
    const title = get("Title");
    const tildaUid = get("TildaUID");
    const externalId = get("External ID");
    const editions = get("Editions");
    invariant(sku.length > 0, "tilda.empty_sku", "Tilda product SKU cannot be empty", { row: rowNumber + 1 });
    invariant(title.length > 0, "tilda.empty_title", "Tilda product title cannot be empty", { row: rowNumber + 1, sku });

    const identity = tildaUid || externalId || `${sku}::${editions}`;
    const quantityRaw = get("Quantity");
    let quantity: number | null = null;
    if (quantityRaw !== "") {
      quantity = Number(quantityRaw);
      invariant(Number.isSafeInteger(quantity) && quantity >= 0, "tilda.invalid_quantity", "Quantity must be a non-negative integer or blank", {
        row: rowNumber + 1,
        sku,
        quantity: quantityRaw,
      });
    }

    const oldPriceRaw = get("Price OLD");
    const product: TildaProductRow = {
      identity,
      ...(tildaUid ? { tildaUid } : {}),
      ...(externalId ? { externalId } : {}),
      ...(get("Parent ID") ? { parentId: get("Parent ID") } : {}),
      sku,
      ...(get("Brand") ? { brand: get("Brand") } : {}),
      ...(get("Category") ? { category: get("Category") } : {}),
      title,
      ...(get("Description") ? { description: get("Description") } : {}),
      ...(get("Photo") ? { photo: get("Photo") } : {}),
      price: Money.parse(get("Price")),
      ...(oldPriceRaw ? { oldPrice: Money.parse(oldPriceRaw) } : {}),
      quantity,
      ...(editions ? { editions } : {}),
    };

    const existing = result.get(identity);
    if (existing) {
      if (JSON.stringify(existing) !== JSON.stringify(product)) {
        throw new DomainError("tilda.conflicting_duplicate", "Tilda feed contains conflicting rows with the same product identity", {
          identity,
          row: rowNumber + 1,
        });
      }
      continue;
    }
    result.set(identity, product);
  }
  return [...result.values()];
}

export function parseSemicolonCsv(source: string): readonly (readonly string[])[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]!;
    if (quoted) {
      if (char === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      invariant(field.length === 0, "tilda.invalid_csv", "Quote can only start at the beginning of a field");
      quoted = true;
    } else if (char === ";") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }

  invariant(!quoted, "tilda.invalid_csv", "CSV contains an unclosed quoted field");
  if (field.length > 0 || row.length > 0) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  return rows;
}
