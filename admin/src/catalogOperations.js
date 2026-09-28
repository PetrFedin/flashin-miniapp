export function normalizeCatalogPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    return { value: null, error: "Цена должна быть конечным числом больше нуля." };
  }
  return { value: Math.round(number * 100) / 100, error: "" };
}

export function normalizeCatalogStock(value, reservedQty = 0) {
  const number = Number(value);
  const reserved = Number(reservedQty || 0);
  if (!Number.isInteger(number) || number < 0) {
    return { value: null, error: "Остаток должен быть целым неотрицательным числом." };
  }
  if (Number.isFinite(reserved) && number < reserved) {
    return {
      value: null,
      error: `Остаток нельзя уменьшить ниже зарезервированного количества (${reserved}).`,
    };
  }
  return { value: number, error: "" };
}

export function normalizeCatalogText(value, label, maxLength) {
  const text = String(value ?? "").trim();
  if (!text) return { value: "", error: `${label} не может быть пустым.` };
  if (text.length > maxLength) {
    return { value: "", error: `${label} превышает допустимую длину ${maxLength} символов.` };
  }
  return { value: text, error: "" };
}

export function availableQty(variant) {
  return Math.max(0, Number(variant?.stock_qty || 0) - Number(variant?.reserved_qty || 0));
}

export function catalogAttentionCount(products) {
  return (Array.isArray(products) ? products : []).reduce((count, product) => {
    if (!product?.active) return count;
    const variants = Array.isArray(product.variants) ? product.variants : [];
    const hasUnavailableVariant = variants.some((variant) => availableQty(variant) <= 0);
    return count + (hasUnavailableVariant ? 1 : 0);
  }, 0);
}
