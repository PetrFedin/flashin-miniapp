import React, { useEffect, useMemo, useState } from "react";
import "./admin-runtime-status.css";

const DATA_EVENT = "flashin-admin-data-status";
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

export default function AdminRuntimeStatus() {
  const [dataStatus, setDataStatus] = useState(null);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    function onDataStatus(event) {
      const detail = event?.detail;
      if (!detail || !Number.isSafeInteger(detail.generation)) return;
      setDataStatus((current) => {
        if (current && current.generation > detail.generation) return current;
        return {
          generation: detail.generation,
          loading: Boolean(detail.loading),
          completed: Number(detail.completed || 0),
          total: Number(detail.total || 0),
          failures: Array.isArray(detail.failures) ? detail.failures.slice(0, 50) : [],
        };
      });
    }

    function onUnhandledRejection(event) {
      setActionError(readableError(event?.reason));
    }

    window.addEventListener(DATA_EVENT, onDataStatus);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    return () => {
      window.removeEventListener(DATA_EVENT, onDataStatus);
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

  if (!dataStatus && !actionError) return null;
  const loading = Boolean(dataStatus?.loading);
  const hasFailures = failures.length > 0;

  return <section className="admin-runtime-status" aria-live="polite">
    {loading && <div className="admin-runtime-status__loading" role="status">
      <strong>Загрузка административных данных</strong>
      <span>{dataStatus.completed} из {dataStatus.total}</span>
      <progress value={dataStatus.completed} max={Math.max(dataStatus.total, 1)} />
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
