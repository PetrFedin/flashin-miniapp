import React, { useEffect, useMemo, useRef, useState } from "react";

import { hasAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson } from "./api.js";

function dateInput(value) {
  if (!value) return "";
  return String(value).replace("Z", "").slice(0, 16);
}

function utcIso(value) {
  if (!value) return null;
  const normalized = String(value).length === 16 ? `${value}:00Z` : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) throw new Error("Некорректная UTC дата/время.");
  return date.toISOString();
}

function rowDraft(row) {
  return {
    promo_price: row.configured_promo_price == null ? "" : String(row.configured_promo_price),
    sale_starts_at: dateInput(row.sale_starts_at),
    sale_ends_at: dateInput(row.sale_ends_at),
  };
}

export default function CatalogPricingPanel({ onUnauthorized, session }) {
  const canRead = hasAdminPermission(session, "products.read");
  const canWrite = hasAdminPermission(session, "products.write");
  const [rows, setRows] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);

  function handleFailure(actionError) {
    if (actionError instanceof AdminApiError && actionError.status === 401) {
      onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      return;
    }
    setError(actionError?.message || "Pricing queue недоступна.");
  }

  async function loadPricing() {
    if (!canRead) return;
    const requestId = sequence.current + 1;
    sequence.current = requestId;
    setLoading(true);
    setError("");
    try {
      const payload = await adminJson("/api/catalog/admin/pricing");
      if (sequence.current !== requestId) return;
      const nextRows = Array.isArray(payload) ? payload : [];
      setRows(nextRows);
      setDrafts(Object.fromEntries(nextRows.map((row) => [row.product_id, rowDraft(row)])));
    } catch (actionError) {
      if (sequence.current === requestId) handleFailure(actionError);
    } finally {
      if (sequence.current === requestId) setLoading(false);
    }
  }

  useEffect(() => {
    loadPricing();
    return () => { sequence.current += 1; };
  }, [canRead]);

  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => `${row.sku} ${row.title}`.toLowerCase().includes(needle));
  }, [query, rows]);

  function patchDraft(productId, field, value) {
    setDrafts((current) => ({
      ...current,
      [productId]: { ...(current[productId] || {}), [field]: value },
    }));
  }

  async function save(row) {
    if (!canWrite || busyId) return;
    const draft = drafts[row.product_id] || rowDraft(row);
    const promoText = String(draft.promo_price || "").trim();
    const promoPrice = promoText ? Number(promoText) : null;
    if (promoPrice != null && (!Number.isFinite(promoPrice) || promoPrice <= 0 || promoPrice >= Number(row.regular_price))) {
      setError(`Promo price для ${row.sku} должна быть > 0 и ниже regular price.`);
      return;
    }
    setBusyId(row.product_id);
    setError("");
    setNotice("");
    try {
      await adminJson(`/api/catalog/admin/products/${row.product_id}/pricing`, {
        method: "PATCH",
        body: JSON.stringify({
          promo_price: promoPrice,
          sale_starts_at: utcIso(draft.sale_starts_at),
          sale_ends_at: utcIso(draft.sale_ends_at),
        }),
        dedupeKey: `catalog-pricing:${row.product_id}`,
      });
      setNotice(`${row.sku}: pricing сохранён.`);
      await loadPricing();
    } catch (actionError) {
      handleFailure(actionError);
    } finally {
      setBusyId(null);
    }
  }

  if (!canRead) return null;

  return (
    <section className="catalog-pricing-panel" aria-labelledby="catalog-pricing-title">
      <div className="section-heading">
        <div>
          <h2 id="catalog-pricing-title">Scheduled pricing</h2>
          <p>Merchandising promo формирует unit price до промокода и loyalty. Все границы вводятся в UTC; конец окна не включён.</p>
        </div>
        <button type="button" onClick={loadPricing} disabled={loading}>{loading ? "Обновление…" : "Обновить"}</button>
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

      <input
        aria-label="Поиск pricing товара"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="SKU или название"
      />

      <div className="table">
        {visibleRows.map((row) => {
          const draft = drafts[row.product_id] || rowDraft(row);
          return (
            <div className="row" key={row.product_id}>
              <b>{row.sku}</b>
              <span>{row.title}</span>
              <span>Regular: {row.regular_price}</span>
              <span>Effective: {row.effective_price ?? "BLOCKED"}</span>
              <span>{row.promo_active ? "PROMO ACTIVE" : "regular"}</span>
              {row.configuration_error && <span role="alert">{row.configuration_error}</span>}
              <label>
                Promo price
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={draft.promo_price}
                  onChange={(event) => patchDraft(row.product_id, "promo_price", event.target.value)}
                  disabled={!canWrite}
                  placeholder="пусто = без promo"
                />
              </label>
              <label>
                Start UTC
                <input
                  type="datetime-local"
                  value={draft.sale_starts_at}
                  onChange={(event) => patchDraft(row.product_id, "sale_starts_at", event.target.value)}
                  disabled={!canWrite}
                />
              </label>
              <label>
                End UTC
                <input
                  type="datetime-local"
                  value={draft.sale_ends_at}
                  onChange={(event) => patchDraft(row.product_id, "sale_ends_at", event.target.value)}
                  disabled={!canWrite}
                />
              </label>
              {canWrite && (
                <button type="button" onClick={() => save(row)} disabled={busyId === row.product_id}>
                  {busyId === row.product_id ? "Сохранение…" : "Сохранить pricing"}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {!loading && !visibleRows.length && <p>Товары не найдены.</p>}
    </section>
  );
}
