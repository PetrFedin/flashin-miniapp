import React, { useCallback, useEffect, useRef, useState } from "react";

import { AdminApiError, adminJson } from "./api.js";
import {
  formatPilotTimestamp,
  normalizePilotOperationsStatus,
  pilotIntegrityLabels,
  pilotStatusLabel,
  pilotStopLabel,
} from "./pilotOperations.js";
import {
  normalizePilotReadiness,
  pilotDiagnosticLabel,
  pilotReadinessCodeLabel,
} from "./pilotReadiness.js";

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

function ReadinessSignals({ readiness }) {
  const criticalEntries = Object.entries(readiness.critical);
  const criticalHealthy = criticalEntries.every(([, value]) => value === true);
  return (
    <>
      <article className={`pilot-integrity-card ${criticalHealthy ? "healthy" : "failed"}`}>
        <div className="pilot-card-heading">
          <h3>Next-order readiness</h3>
          <span className={`pilot-badge ${criticalHealthy ? "healthy" : "failed"}`}>
            {criticalHealthy ? "Критические сервисы OK" : "Есть блокирующий сигнал"}
          </span>
        </div>
        <dl className="pilot-metrics-list">
          {criticalEntries.map(([name, value]) => (
            <div key={name}>
              <dt>{pilotDiagnosticLabel(name)}</dt>
              <dd>{value === true ? "OK" : value === false ? "NO-GO" : "Нет сигнала"}</dd>
            </div>
          ))}
        </dl>
      </article>

      {readiness.warningCodes.length > 0 && (
        <article className="pilot-integrity-card not-applicable">
          <div className="pilot-card-heading">
            <h3>Advisory</h3>
            <span className="pilot-badge not-applicable">Не блокирует деньги</span>
          </div>
          <ul>
            {readiness.warningCodes.map((code) => (
              <li key={code}>{pilotReadinessCodeLabel(code)}</li>
            ))}
          </ul>
        </article>
      )}
    </>
  );
}

export default function PilotOperationsPanel({ onUnauthorized }) {
  const [status, setStatus] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [loadError, setLoadError] = useState("");
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const unauthorizedHandler = useRef(onUnauthorized);

  useEffect(() => {
    unauthorizedHandler.current = onUnauthorized;
  }, [onUnauthorized]);

  const clearSnapshot = useCallback(() => {
    if (!mounted.current) return;
    setStatus(null);
    setReadiness(null);
  }, []);

  const loadStatus = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (mounted.current) {
      setLoading(true);
      setLoadError("");
    }
    try {
      const [readinessPayload, runtimePayload] = await Promise.all([
        adminJson("/api/ops/pilot-readiness", {
          headers: { "Cache-Control": "no-cache" },
        }),
        adminJson("/api/ops/pilot-runtime", {
          headers: { "Cache-Control": "no-cache" },
        }),
      ]);
      if (!mounted.current) return;
      setReadiness(normalizePilotReadiness(readinessPayload));
      setStatus(normalizePilotOperationsStatus(runtimePayload));
      setAccessDenied(false);
    } catch (error) {
      if (!mounted.current) return;
      clearSnapshot();
      if (error instanceof AdminApiError && error.status === 401) {
        unauthorizedHandler.current?.("Сессия администратора истекла. Войдите снова.");
        return;
      }
      if (error instanceof AdminApiError && error.status === 403) {
        setAccessDenied(true);
        setLoadError("");
        return;
      }
      setLoadError("Не удалось обновить readiness. Предыдущий статус сброшен в NO-GO.");
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [clearSnapshot]);

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

  const combinedDecision = !loading
    && readiness?.decision === "GO"
    && status?.decision === "GO"
    ? "GO"
    : "NO-GO";

  return (
    <section className="pilot-panel" aria-live="polite">
      <div className="section-heading">
        <div>
          <h2>Контролируемый пилот</h2>
          <p>Read-only cockpit первых 20 заказов. Любая потеря свежего сигнала трактуется как NO-GO.</p>
        </div>
        <button type="button" onClick={loadStatus} disabled={loading}>
          {loading ? "Проверка…" : "Обновить статус"}
        </button>
      </div>

      {loadError && <div className="pilot-inline-error" role="alert">{loadError}</div>}
      {!status && !readiness && !loadError && loading && (
        <p>Проверяем diagnostics, migrations, runtime, release capability и денежные сигналы…</p>
      )}

      {status && readiness && (
        <>
          <div className={`pilot-decision ${combinedDecision === "GO" ? "go" : "no-go"}`}>
            <div>
              <span className="pilot-decision-label">Решение для следующего checkout</span>
              <strong>{combinedDecision}</strong>
            </div>
            <div className="pilot-decision-meta">
              <span>Runtime: {pilotStatusLabel(status.runtime.status)}</span>
              <span>Enforcement: {status.enforced ? "включён" : "выключен"}</span>
              <span>Проверено: {formatPilotTimestamp(status.generatedAt)}</span>
            </div>
          </div>

          {readiness.blockingCodes.length > 0 && (
            <div className="pilot-inline-error" role="alert">
              <strong>Следующий пилотный заказ заблокирован:</strong>
              <ul>
                {readiness.blockingCodes.map((code) => (
                  <li key={code}>{pilotReadinessCodeLabel(code)}</li>
                ))}
              </ul>
            </div>
          )}

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
            <ReadinessSignals readiness={readiness} />
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

          {(!status.contractValid || !readiness.contractValid) && (
            <div className="pilot-inline-error" role="alert">
              Контракт одного из safety-ответов не подтверждён. Панель принудительно показывает NO-GO.
            </div>
          )}
        </>
      )}
    </section>
  );
}
