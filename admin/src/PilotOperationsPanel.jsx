import React, { useCallback, useEffect, useRef, useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import {
  formatPilotTimestamp,
  normalizePilotOperationsStatus,
  pilotIntegrityLabels,
  pilotStatusLabel,
  pilotStopLabel,
} from "./pilotOperations.js";

const REFRESH_INTERVAL_MS = 30_000;

function IntegrityCard({ title, healthy, applicable = true, codes }) {
  const labels = pilotIntegrityLabels(codes);
  const state = !applicable ? "not-applicable" : healthy ? "healthy" : "failed";
  const status = !applicable ? "Не применимо" : healthy ? "Подтверждено" : "Нарушение";
  return (
    <article className={`pilot-integrity-card ${state}`}>
      <div className="pilot-card-heading">
        <h3>{title}</h3>
        <span className={`pilot-badge ${state}`}>{status}</span>
      </div>
      {labels.length ? (
        <ul>
          {labels.map((label) => <li key={label}>{label}</li>)}
        </ul>
      ) : (
        <p>{applicable ? "Ошибок не обнаружено." : "Runtime ещё не привязан к admission."}</p>
      )}
    </article>
  );
}

function MoneyAttention({ status }) {
  const money = status.moneyAttention;
  return (
    <article className={`pilot-integrity-card ${money.attentionRequired ? "failed" : "healthy"}`}>
      <div className="pilot-card-heading">
        <h3>Денежные операции</h3>
        <span className={`pilot-badge ${money.attentionRequired ? "failed" : "healthy"}`}>
          {money.attentionRequired ? "Требует проверки" : "Без сигналов"}
        </span>
      </div>
      <dl className="pilot-metrics-list">
        <div><dt>Payment review</dt><dd>{money.paymentReviewOrders}</dd></div>
        <div><dt>Refund retry/review</dt><dd>{money.refundAttentionOrders}</dd></div>
        <div><dt>Reconciliation mismatch</dt><dd>{money.reconciliationMismatches}</dd></div>
      </dl>
    </article>
  );
}

export default function PilotOperationsPanel({ onUnauthorized }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [loadError, setLoadError] = useState("");
  const inFlight = useRef(false);
  const mounted = useRef(true);

  const loadStatus = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (mounted.current) {
      setLoading(true);
      setLoadError("");
    }
    try {
      const payload = await adminJson("/api/ops/pilot-runtime", {
        headers: { "Cache-Control": "no-cache" },
      });
      if (!mounted.current) return;
      setStatus(normalizePilotOperationsStatus(payload));
      setAccessDenied(false);
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof AdminApiError && error.status === 401) {
        onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
        return;
      }
      if (error instanceof AdminApiError && error.status === 403) {
        setAccessDenied(true);
        setStatus(null);
        setLoadError("");
        return;
      }
      setLoadError("Не удалось получить безопасный статус пилота.");
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [onUnauthorized]);

  useEffect(() => {
    mounted.current = true;
    loadStatus();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") loadStatus();
    }, REFRESH_INTERVAL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [loadStatus]);

  if (accessDenied) {
    return (
      <section className="pilot-panel">
        <div className="section-heading">
          <div>
            <h2>Контролируемый пилот</h2>
            <p>Для просмотра требуется permission security.read.</p>
          </div>
          <span className="pilot-badge not-applicable">Нет доступа</span>
        </div>
      </section>
    );
  }

  return (
    <section className="pilot-panel" aria-live="polite">
      <div className="section-heading">
        <div>
          <h2>Контролируемый пилот</h2>
          <p>Read-only статус первых 20 заказов. Управление runtime здесь недоступно.</p>
        </div>
        <button type="button" onClick={loadStatus} disabled={loading}>
          {loading ? "Проверка…" : "Обновить статус"}
        </button>
      </div>

      {loadError && <div className="pilot-inline-error" role="alert">{loadError}</div>}
      {!status && !loadError && loading && <p>Проверяем runtime, release capability и денежные сигналы…</p>}

      {status && (
        <>
          <div className={`pilot-decision ${status.decision === "GO" ? "go" : "no-go"}`}>
            <div>
              <span className="pilot-decision-label">Решение для следующего checkout</span>
              <strong>{status.decision}</strong>
            </div>
            <div className="pilot-decision-meta">
              <span>Runtime: {pilotStatusLabel(status.runtime.status)}</span>
              <span>Enforcement: {status.enforced ? "включён" : "выключен"}</span>
              <span>Проверено: {formatPilotTimestamp(status.generatedAt)}</span>
            </div>
          </div>

          <div className="pilot-kpis">
            <article><span>Принято заказов</span><strong>{status.runtime.acceptedOrders} / {status.runtime.maxOrders}</strong></article>
            <article><span>Осталось слотов</span><strong>{status.runtime.remainingOrders}</strong></article>
            <article><span>Фактические slots</span><strong>{status.runtime.slotCount}</strong></article>
            <article><span>Пользователи allowlist</span><strong>{status.runtime.allowlistCount}</strong></article>
          </div>

          <div className="pilot-runtime-meta">
            <div><span>Run reference</span><b>{status.runtime.runRef || "—"}</b></div>
            <div><span>Исторические slots</span><b>{status.runtime.historicalSlotCount}</b></div>
            <div><span>Последнее изменение</span><b>{formatPilotTimestamp(status.runtime.updatedAt)}</b></div>
            <div><span>Причина STOP</span><b>{status.runtime.stopReason ? pilotStopLabel(status.runtime.stopReason) : "—"}</b></div>
          </div>

          <div className="pilot-integrity-grid">
            <IntegrityCard
              title="Целостность БД"
              healthy={status.databaseIntegrity.healthy}
              codes={status.databaseIntegrity.codes}
            />
            <IntegrityCard
              title="Evidence и releases"
              applicable={status.artifactIntegrity.applicable}
              healthy={status.artifactIntegrity.healthy}
              codes={status.artifactIntegrity.codes}
            />
            <MoneyAttention status={status} />
          </div>

          {!status.contractValid && (
            <div className="pilot-inline-error" role="alert">
              Контракт ответа не подтверждён. Панель принудительно показывает NO-GO.
            </div>
          )}
        </>
      )}
    </section>
  );
}
