import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./scheduled-jobs.css";

const STATUS_LABELS = {
  running: "Выполняется",
  succeeded: "Успешно",
  failed: "Ошибка",
  skipped: "Пропущено",
};

const TRIGGER_LABELS = {
  scheduler: "Планировщик",
  worker: "Worker",
  manual: "Вручную",
  api: "API",
  test: "Тест",
};

function parseApiError(error) {
  const raw = String(error?.message || error || "Неизвестная ошибка");
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) {
      const suffix = detail.run_id ? ` (run #${detail.run_id})` : "";
      return `${detail.message}${suffix}`;
    }
  } catch {
    // The shared API helper may return plain text for proxy or network failures.
  }
  return raw.slice(0, 1000);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(date);
}

function formatDuration(value) {
  if (value === null || value === undefined) return "—";
  if (value < 1000) return `${value} мс`;
  if (value < 60_000) return `${(value / 1000).toFixed(1)} с`;
  return `${(value / 60_000).toFixed(1)} мин`;
}

function resultPreview(result) {
  try {
    return JSON.stringify(result ?? {}, null, 2).slice(0, 8000);
  } catch {
    return "Результат не удалось отобразить";
  }
}

export default function ScheduledJobsPanel({ api }) {
  const [definitions, setDefinitions] = useState([]);
  const [runs, setRuns] = useState([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(0);
  const [total, setTotal] = useState(0);
  const [jobName, setJobName] = useState("");
  const [status, setStatus] = useState("");
  const [trigger, setTrigger] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionKey, setActionKey] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const requestVersion = useRef(0);

  const definitionMap = useMemo(
    () => new Map(definitions.map((item) => [item.name, item])),
    [definitions],
  );

  const loadDefinitions = useCallback(async () => {
    const data = await api("/api/ops/jobs/definitions");
    setDefinitions(Array.isArray(data) ? data : []);
  }, [api]);

  const loadRuns = useCallback(async (targetPage = 1) => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ page: String(targetPage), limit: "25" });
    if (jobName) params.set("job_name", jobName);
    if (status) params.set("status", status);
    if (trigger) params.set("trigger", trigger);
    try {
      const data = await api(`/api/ops/jobs/runs?${params.toString()}`);
      if (version !== requestVersion.current) return;
      setRuns(Array.isArray(data?.items) ? data.items : []);
      setPage(Number(data?.page || targetPage));
      setPages(Number(data?.pages || 0));
      setTotal(Number(data?.total || 0));
    } catch (requestError) {
      if (version === requestVersion.current) setError(parseApiError(requestError));
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [api, jobName, status, trigger]);

  const loadDetail = useCallback(async (runId) => {
    setDetailLoading(true);
    setError("");
    try {
      setSelected(await api(`/api/ops/jobs/runs/${runId}`));
    } catch (requestError) {
      setError(parseApiError(requestError));
    } finally {
      setDetailLoading(false);
    }
  }, [api]);

  useEffect(() => {
    let active = true;
    Promise.all([loadDefinitions(), loadRuns(1)]).catch((requestError) => {
      if (active) setError(parseApiError(requestError));
    });
    return () => {
      active = false;
      requestVersion.current += 1;
    };
  }, [loadDefinitions, loadRuns]);

  async function executeJob(name) {
    const definition = definitionMap.get(name);
    if (!definition?.manual_enabled || actionKey) return;
    if (!window.confirm(`Запустить «${definition.title}» вручную?`)) return;
    setActionKey(`run:${name}`);
    setError("");
    setNotice("");
    try {
      const run = await api(`/api/ops/jobs/${encodeURIComponent(name)}/run`, {
        method: "POST",
      });
      setNotice(`Задание завершено: run #${run.id}, статус «${STATUS_LABELS[run.status] || run.status}».`);
      setSelected(run);
      await loadRuns(1);
    } catch (requestError) {
      setError(parseApiError(requestError));
      await loadRuns(1);
    } finally {
      setActionKey("");
    }
  }

  async function retryRun(run) {
    if (!run || !["failed", "skipped"].includes(run.status) || actionKey) return;
    const definition = definitionMap.get(run.job_name);
    if (!definition?.retry_enabled) return;
    if (!window.confirm(`Повторить run #${run.id} задания «${definition.title}»?`)) return;
    setActionKey(`retry:${run.id}`);
    setError("");
    setNotice("");
    try {
      const retried = await api(`/api/ops/jobs/runs/${run.id}/retry`, { method: "POST" });
      setNotice(`Создан повторный запуск #${retried.id}.`);
      setSelected(retried);
      await loadRuns(1);
    } catch (requestError) {
      setError(parseApiError(requestError));
      await loadRuns(page);
    } finally {
      setActionKey("");
    }
  }

  const failedCount = runs.filter((item) => item.status === "failed").length;
  const runningCount = runs.filter((item) => item.status === "running").length;

  return <div className="scheduled-jobs">
    <div className="scheduled-jobs__heading">
      <div>
        <h3>Фоновые задания</h3>
        <p>Защищённые ручные запуски, история, ошибки и повторные попытки.</p>
      </div>
      <button type="button" onClick={() => loadRuns(page)} disabled={loading || Boolean(actionKey)}>
        {loading ? "Обновление…" : "Обновить"}
      </button>
    </div>

    <div className="scheduled-jobs__metrics" aria-label="Сводка фоновых заданий">
      <div><span>Доступно</span><strong>{definitions.length}</strong></div>
      <div><span>Запусков</span><strong>{total}</strong></div>
      <div><span>В работе на странице</span><strong>{runningCount}</strong></div>
      <div><span>Ошибок на странице</span><strong>{failedCount}</strong></div>
    </div>

    {notice && <div className="scheduled-jobs__notice" role="status">{notice}</div>}
    {error && <div className="scheduled-jobs__error" role="alert">{error}</div>}

    <div className="scheduled-jobs__definitions">
      {definitions.map((definition) => <article key={definition.name}>
        <div>
          <strong>{definition.title}</strong>
          <p>{definition.description}</p>
          <small>{definition.kind === "async" ? "Асинхронное" : "Синхронное"} · {definition.name}</small>
        </div>
        <button
          type="button"
          onClick={() => executeJob(definition.name)}
          disabled={!definition.manual_enabled || Boolean(actionKey)}
        >
          {actionKey === `run:${definition.name}` ? "Выполняется…" : "Запустить"}
        </button>
      </article>)}
    </div>

    <div className="scheduled-jobs__filters">
      <label>Задание
        <select value={jobName} onChange={(event) => { setJobName(event.target.value); setPage(1); }}>
          <option value="">Все</option>
          {definitions.map((definition) => <option key={definition.name} value={definition.name}>{definition.title}</option>)}
        </select>
      </label>
      <label>Статус
        <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
          <option value="">Все</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>Источник
        <select value={trigger} onChange={(event) => { setTrigger(event.target.value); setPage(1); }}>
          <option value="">Все</option>
          {Object.entries(TRIGGER_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <button type="button" onClick={() => loadRuns(1)} disabled={loading}>Применить</button>
    </div>

    <div className="scheduled-jobs__table-wrap">
      <table className="scheduled-jobs__table">
        <thead><tr>
          <th>Run</th><th>Задание</th><th>Статус</th><th>Источник</th><th>Начало</th><th>Время</th><th>Действия</th>
        </tr></thead>
        <tbody>
          {runs.map((run) => {
            const definition = definitionMap.get(run.job_name);
            const canRetry = ["failed", "skipped"].includes(run.status) && definition?.retry_enabled;
            return <tr key={run.id}>
              <td>#{run.id}</td>
              <td><strong>{definition?.title || run.job_name}</strong><small>{run.job_name}</small></td>
              <td><span className={`scheduled-jobs__status scheduled-jobs__status--${run.status}`}>{STATUS_LABELS[run.status] || run.status}</span></td>
              <td>{TRIGGER_LABELS[run.trigger] || run.trigger}</td>
              <td>{formatDate(run.started_at)}</td>
              <td>{formatDuration(run.duration_ms)}</td>
              <td className="scheduled-jobs__actions">
                <button type="button" onClick={() => loadDetail(run.id)} disabled={detailLoading}>Детали</button>
                {canRetry && <button type="button" onClick={() => retryRun(run)} disabled={Boolean(actionKey)}>
                  {actionKey === `retry:${run.id}` ? "Повтор…" : "Повторить"}
                </button>}
              </td>
            </tr>;
          })}
          {!runs.length && <tr><td colSpan="7" className="scheduled-jobs__empty">{loading ? "Загрузка…" : "Запуски не найдены"}</td></tr>}
        </tbody>
      </table>
    </div>

    <div className="scheduled-jobs__pagination">
      <button type="button" onClick={() => loadRuns(page - 1)} disabled={loading || page <= 1}>Назад</button>
      <span>Страница {page} из {Math.max(pages, 1)}</span>
      <button type="button" onClick={() => loadRuns(page + 1)} disabled={loading || page >= pages}>Вперёд</button>
    </div>

    {selected && <aside className="scheduled-jobs__detail" aria-live="polite">
      <div className="scheduled-jobs__detail-heading">
        <h4>Run #{selected.id}</h4>
        <button type="button" onClick={() => setSelected(null)}>Закрыть</button>
      </div>
      <dl>
        <div><dt>Задание</dt><dd>{definitionMap.get(selected.job_name)?.title || selected.job_name}</dd></div>
        <div><dt>Статус</dt><dd>{STATUS_LABELS[selected.status] || selected.status}</dd></div>
        <div><dt>Worker</dt><dd>{selected.worker_id || "—"}</dd></div>
        <div><dt>Начало</dt><dd>{formatDate(selected.started_at)}</dd></div>
        <div><dt>Окончание</dt><dd>{formatDate(selected.finished_at)}</dd></div>
        <div><dt>Длительность</dt><dd>{formatDuration(selected.duration_ms)}</dd></div>
      </dl>
      {selected.error && <div className="scheduled-jobs__detail-error"><strong>Ошибка</strong><pre>{String(selected.error).slice(0, 2000)}</pre></div>}
      <strong>Результат</strong>
      <pre>{resultPreview(selected.result)}</pre>
      {["failed", "skipped"].includes(selected.status) && definitionMap.get(selected.job_name)?.retry_enabled && <button
        type="button"
        onClick={() => retryRun(selected)}
        disabled={Boolean(actionKey)}
      >Повторить запуск</button>}
    </aside>}
  </div>;
}
