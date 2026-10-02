import * as Sentry from "@sentry/react";

const RELEASE_SHA = typeof __FLASHIN_RELEASE_SHA__ === "string"
  ? __FLASHIN_RELEASE_SHA__
  : "unknown";

function scrubEvent(event) {
  const next = { ...event };
  delete next.user;
  delete next.request;
  next.breadcrumbs = [];
  if (next.contexts?.trace) next.contexts = { trace: next.contexts.trace };
  else next.contexts = {};
  return next;
}

export function initAdminObservability() {
  const dsn = String(import.meta.env.VITE_SENTRY_DSN || "").trim();
  if (!dsn) return false;
  Sentry.init({
    dsn,
    release: RELEASE_SHA,
    environment: import.meta.env.MODE,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
    beforeSend: scrubEvent,
    initialScope: { tags: { surface: "admin" } },
  });
  return true;
}

export function captureAdminApiError(error, action = "") {
  const status = Number(error?.status || 0);
  const safeError = new Error(`FLASHIN Admin API error status=${status || "network"}`);
  safeError.name = "FlashinAdminApiError";
  Sentry.withScope((scope) => {
    scope.setTag("request_id", String(error?.requestId || "missing"));
    scope.setTag("trace_id", String(error?.traceId || "missing"));
    scope.setTag("api_status", String(status || "network"));
    if (action) scope.setTag("action", String(action));
    Sentry.captureException(safeError);
  });
}

export const adminReleaseSha = RELEASE_SHA;
