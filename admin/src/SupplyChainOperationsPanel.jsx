import React, { useEffect, useRef, useState } from "react";

import { hasAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson } from "./api.js";
import {
  normalizeSupplyChainStatus,
  syncStatusLabel,
  syncTypeLabel,
} from "./supplyChainOperations.js";

const REFRESH_INTERVAL_MS = 30_000;

function safeOperationError(error) {
  if (error instanceof AdminApiError && error.status === 403) {
    return "Недостаточно прав для изменения данных МойСклад. Просмотр статуса остаётся доступен.";
  }
  if (error instanceof AdminApiError && error.status === 404) {
    return "Запрошенная запись МойСклад больше не существует. Обновите данные.";
  }
  if (error instanceof AdminApiError && error.status >= 500) {
    return "Операция МойСклад завершилась серверной ошибкой. Проверьте безопасный статус и журнал операций.";
  }
  return "Операция МойСклад не выполнена. Обновите статус перед повторной попыткой.";
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("ru-RU");
}

export default function SupplyChainOperationsPanel({ onUnauthorized, session }) {
  const canMutate = hasAdminPermission(session, "products.write");
  const [snapshot, setSnapshot] = useState(() => normalizeSupplyChainStatus(null));
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [confirmingId, setConfirmingId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const requestSequence = useRef(0);
  const mutationLocks = useRef(new Set());

  async function loadStatus({ silent = false } = {}) {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    if (!silent) setLoading(true);
    try {
      const payload = await adminJson("/api/moysklad/operations-status");
      if (requestSequence.current !== sequence) return;
      const normalized = normalizeSupplyChainStatus(payload);
      setSnapshot(normalized);
      if (!normalized.valid) {
        setError("Контракт Supply Chain status некорректен. Состояние считается требующим внимания.");
      } else if (!silent) {
        setError("");
      }
    } catch (actionError) {
      if (requestSequence.current !== sequence) return;
      setSnapshot(normalizeSupplyChainStatus(null));
      if (actionError instanceof AdminApiError && actionError.status === 401) {
        onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      } else if (actionError instanceof AdminApiError && actionError.status === 403) {
        setError("Недостаточно прав для просмотра Supply Chain status.");
      } else {
        setError("Supply Chain status недоступен. До обновления считайте синхронизацию требующей внимания.");
      }
    } finally {
      if (!silent && requestSequence.current === sequence) setLoading(false);
    }
  }

  useEffect(() => {
    loadStatus();
    const timer = window.setInterval(() => loadStatus({ silent: true }), REFRESH_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
      requestSequence.current += 1;
    };
  }, []);

  async function runMutation(key, operation) {
    if (!canMutate) {
      setError("Недостаточно прав: изменение данных МойСклад требует products.write.");
      return null;
    }
    if (mutationLocks.current.has(key)) return null;
    mutationLocks.current.add(key);
    setError("");
    setNotice("");
    try {
      return await operation();
    } catch (actionError) {
      if (actionError instanceof AdminApiError && actionError.status === 401) {
        onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      } else {
        setError(safeOperationError(actionError));
      }
      return null;
    } finally {
      mutationLocks.current.delete(key);
    }
  }

  async function startSync() {
    if (!canMutate) return;
    const confirmed = window.confirm(
      "Запустить ручную синхронизацию с МойСклад? Она может изменить названия, описания, цены, категории и физические остатки. Зарезервированный stock не будет уменьшен ниже резерва.",
    );
    if (!confirmed) return;

    setSyncing(true);
    const result = await runMutation("manual-sync", () => adminJson("/api/moysklad/sync", { method: "POST" }));
    setSyncing(false);
    if (!result) return;

    if (String(result.status || "").toLowerCase() === "success") {
      setNotice("Ручная синхронизация МойСклад завершена. Проверьте новые расхождения и сопоставления.");
    } else {
      setError("Ручная синхронизация завершилась ошибкой. Raw provider error скрыт; используйте безопасный статус и серверный журнал.");
    }
    await loadStatus({ silent: true });
  }

  async function confirmMatch(match) {
    if (!canMutate || match.confirmed || !match.id) return;
    const confirmed = window.confirm(
      `Подтвердить сопоставление SKU ${match.localSku || "—"} ↔ ${match.externalSku || "—"}? `
      + "После подтверждения автоматический sync сможет считать это соответствие доверенным.",
    );
    if (!confirmed) return;

    setConfirmingId(match.id);
    const result = await runMutation(
      `confirm-match-${match.id}`,
      () => adminJson(`/api/moysklad-deep-mapping/sku-matches/${match.id}/confirm`, { method: "POST" }),
    );
    setConfirmingId(null);
    if (!result) return;
    setNotice(`Сопоставление ${match.localSku || match.id} подтверждено.`);
    await loadStatus({ silent: true });
  }

  const summary = snapshot.summary;
  const attentionClass = snapshot.attentionRequired ? "attention" : "ok";

  return (
    <section className="service-operations" aria-labelledby="supply-chain-operations-title">
      <div className="section-title-row">
        <div>
          <h2 id="supply-chain-operations-title">Supply Chain · МойСклад</h2>
          <p>Синхронизация ассортимента, сопоставления SKU и расхождения stock без provider secrets.</p>
        </div>
        <div className={`attention-badge ${attentionClass}`}>
          {snapshot.attentionRequired ? "Требует внимания" : "Без конфликтов"}
        </div>
        <button type="button" onClick={() => loadStatus()} disabled={loading}>
          {loading ? "Обновление…" : "Обновить Supply Chain"}
        </button>
        {canMutate && (
          <button type="button" className="danger" onClick={startSync} disabled={syncing}>
            {syncing ? "Синхронизация…" : "Синхронизировать с МойСклад"}
          </button>
        )}
      </div>

      {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}
      {!canMutate && <p className="event-warning">Supply Chain доступен только для чтения: нет products.write.</p>}

      <div className="kpis event-kpis" aria-label="Supply Chain summary">
        <div><span>Последний sync</span><strong>{syncStatusLabel(summary.lastSyncStatus)}</strong><small>{formatTimestamp(summary.lastSyncAt)}</small></div>
        <div><span>SKU ждут подтверждения</span><strong>{summary.pendingMatches}</strong></div>
        <div><span>Stock расхождения</span><strong>{summary.openReconciliations}</strong></div>
        <div><span>Конфликты</span><strong>{summary.openConflicts}</strong></div>
      </div>

      <div className="service-grid">
        <article className="service-card" aria-labelledby="moysklad-sync-history-title">
          <h3 id="moysklad-sync-history-title">Последние синхронизации</h3>
          {!snapshot.syncLogs.length && <p>История синхронизации отсутствует.</p>}
          {snapshot.syncLogs.map((row) => (
            <div className="service-item" key={row.id}>
              <div className="service-item-heading">
                <b>{syncTypeLabel(row.syncType)}</b>
                <span>{syncStatusLabel(row.status)}</span>
              </div>
              <small>
                {formatTimestamp(row.finishedAt || row.createdAt)} · увидено {row.productsSeen} · товаров {row.productsUpserted} · SKU {row.variantsUpserted}
                {row.hasError ? " · есть скрытая ошибка" : ""}
              </small>
            </div>
          ))}
        </article>

        <article className="service-card" aria-labelledby="moysklad-matches-title">
          <h3 id="moysklad-matches-title">Сопоставления SKU</h3>
          {!snapshot.skuMatches.length && <p>Сопоставления отсутствуют.</p>}
          {snapshot.skuMatches.map((match) => (
            <div className="service-item" key={match.id}>
              <div className="service-item-heading">
                <b>{match.localSku || `Variant #${match.localVariantId}`}</b>
                <span>{match.confirmed ? "Подтверждено" : "Ожидает подтверждения"}</span>
              </div>
              <small>МойСклад SKU: {match.externalSku || "—"} · confidence {(match.confidence * 100).toFixed(0)}%</small>
              {canMutate && !match.confirmed && (
                <button
                  type="button"
                  onClick={() => confirmMatch(match)}
                  disabled={confirmingId === match.id}
                >
                  {confirmingId === match.id ? "Подтверждение…" : `Подтвердить SKU ${match.localSku || match.id}`}
                </button>
              )}
            </div>
          ))}
        </article>

        <article className="service-card" aria-labelledby="stock-reconciliation-title">
          <h3 id="stock-reconciliation-title">Stock reconciliation</h3>
          {!snapshot.reconciliations.length && <p>Расхождений stock нет.</p>}
          {snapshot.reconciliations.map((row) => (
            <div className="service-item" key={row.id}>
              <div className="service-item-heading">
                <b>{row.sku || `Variant #${row.variantId}`}</b>
                <span>{row.status}</span>
              </div>
              <small>
                local {row.localStockQty} · reserved {row.localReservedQty} · MoySklad {row.externalStockQty} · Δ {row.delta}
              </small>
            </div>
          ))}
        </article>

        <article className="service-card" aria-labelledby="moysklad-conflicts-title">
          <h3 id="moysklad-conflicts-title">Конфликты импорта</h3>
          {!snapshot.conflicts.length && <p>Открытых конфликтов нет.</p>}
          {snapshot.conflicts.map((row) => (
            <div className="service-item" key={row.id}>
              <div className="service-item-heading">
                <b>{row.sku || "SKU не указан"}</b>
                <span>{row.status}</span>
              </div>
              <small>{row.conflictType || "Неизвестный тип"} · {formatTimestamp(row.createdAt)}</small>
            </div>
          ))}
        </article>
      </div>
    </section>
  );
}
