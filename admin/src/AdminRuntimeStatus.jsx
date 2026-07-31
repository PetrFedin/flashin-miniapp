import React, { useEffect, useMemo, useState } from "react";
import "./admin-runtime-status.css";

const DATA_EVENT = "flashin-admin-data-status";
const ACTION_EVENT = "flashin-admin-action-status";
const MAX_MESSAGE = 1000;

function readableError(value) {
  const raw = String(value?.message || value || "Неизвестная ошибка").trim();
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail.slice(0, MAX_MESSAGE);
    if (detail?.message) return String(detail.message).slice(0, MAX_MESSAGE);
  } catch {
    // Browser, proxy and network errors may not be JSON.
  }
  return raw.slice(0, MAX_MESSAGE);
}

function normalizeDataStatus(detail) {
  if (!detail || !Number.isSafeInteger(detail.generation)) return null;
  return {
    generation: detail.generation,
    loading: Boolean(detail.loading),
    completed: Number(detail.completed || 0),
    total: Number(detail.total || 0),
    failures: Array.isArray(detail.failures) ? detail.failures.slice(0, 50) : [],
  };
}

function normalizeActionStatus(detail) {
  if (!detail || !Array.isArray(detail.active)) return null;
  const active = detail.active.slice(0, 20).map((item) => ({
    id: Number(item.id || 0),
    method: String(item.method || ""),
    path: String(item.path || "").slice(0, 300),
    startedAt: Number(item.startedAt || 0),
    duplicates: Math.max(0, Number(item.duplicates || 0)),
  }));
  const last = detail.last && typeof detail.last === "object"
    ? {
        id: Number(detail.last.id || 0),
        type: String(detail.last.type || ""),
        method: String(detail.last.method || ""),
        path: String(detail.last.path || "").slice(0, 300),
        status: Number(detail.last.status || 0),
        durationMs: Number(detail.last.durationMs || 0),
        duplicates: Math.max(0, Number(detail.last.duplicates || 0)),
        message: String(detail.last.message || "").slice(0, MAX_MESSAGE),
      }
    : null;
  return {
    active,
    activeCount: active.length,
    last,
  };
}

function actionFailureMessage(last) {
  if (!last || last.type !== "failed") return "";
  if (last.message) return last.message;
  const status = last.status ? `HTTP ${last.status}` : "сетевая ошибка";
  return `${last.method} ${last.path}: ${status}`;
}

export default function AdminRuntimeStatus() {
  const [dataStatus, setDataStatus] = useState(
    () => normalizeDataStatus(window.__flashinAdminDataStatus),
  );
  const [actionStatus, setActionStatus] = useState(
    () => normalizeActionStatus(window.__flashinAdminActionStatus),
  );
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    function onDataStatus(event) {
      const next = normalizeDataStatus(event?.detail);
      if (!next) return;
      setDataStatus((current) => {
        if (current && current.generation > next.generation) return current;
        return next;
      });
    }

    function onActionStatus(event) {
      const next = normalizeActionStatus(event?.detail);
      if (!next) return;
      setActionStatus(next);
      const failure = actionFailureMessage(next.last);
      if (failure) setActionError(failure);
    }

    function onUnhandledRejection(event) {
      setActionError(readableError(event?.reason));
    }

    window.addEventListener(DATA_EVENT, onDataStatus);
    window.addEventListener(ACTION_EVENT, onActionStatus);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    return () => {
      window.removeEventListener(DATA_EVENT, onDataStatus);
      window.removeEventListener(ACTION_EVENT, onActionStatus);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);

  const failures = dataStatus?.failures || [];
  const failureSummary = useMemo(
    () => failures.map((failure) => {
      const status = failure.status ? `HTTP ${failure.status}` : "сеть";
      return `${failure.label || failure.path}: ${status}`;
    }),
    [failures],
  );
  const activeActions = actionStatus?.active || [];
  const duplicateCount = activeActions.reduce(
    (total, item) => total + item.duplicates,
    0,
  );
  const activeSummary = activeActions
    .slice(0, 4)
    .map((item) => `${item.method} ${item.path}`)
    .join("; ");

  if (!dataStatus && !actionStatus && !actionError) return null;
  const loading = Boolean(dataStatus?.loading);
  const hasFailures = failures.length > 0;

  return <section className="admin-runtime-status" aria-live="polite">
    {loading && <div className="admin-runtime-status__loading" role="status">
      <strong>Загрузка административных данных</strong>
      <span>{dataStatus.completed} из {dataStatus.total}</span>
      <progress value={dataStatus.completed} max={Math.max(dataStatus.total, 1)} />
    </div>}

    {activeActions.length > 0 && <div className="admin-runtime-status__actions" role="status">
      <div>
        <strong>Выполняются операции: {activeActions.length}</strong>
        <p>{activeSummary}{activeActions.length > 4 ? "; …" : ""}</p>
        {duplicateCount > 0 && <small>Повторных запросов подавлено: {duplicateCount}</small>}
      </div>
      <progress />
    </div>}

    {!loading && hasFailures && <div className="admin-runtime-status__warning" role="alert">
      <div>
        <strong>Часть разделов временно недоступна</strong>
        <p>{failureSummary.join("; ")}</p>
        <small>Остальные разделы загружены и продолжают работать независимо.</small>
      </div>
      <button type="button" onClick={() => window.location.reload()}>Повторить загрузку</button>
    </div>}

    {actionError && <div className="admin-runtime-status__error" role="alert">
      <div>
        <strong>Операция не завершена</strong>
        <p>{actionError}</p>
      </div>
      <button type="button" onClick={() => setActionError("")}>Закрыть</button>
    </div>}
  </section>;
}
