import * as Sentry from "@sentry/react";

const RELEASE_SHA = typeof __FLASHIN_RELEASE_SHA__ === "string"
  ? __FLASHIN_RELEASE_SHA__
  : "unknown";

function scrubEvent(event) {
  const next = { ...event };
  delete next.user;
  delete next.request;
  next.breadcrumbs = [];
  if (next.contexts?.trace) {
    next.contexts = { trace: next.contexts.trace };
  } else {
    next.contexts = {};
  }
  return next;
}

export function initFrontendObservability(surface = "mini-app") {
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
    initialScope: {
      tags: {
        surface,
      },
    },
  });
  return true;
}

export function captureApiError(error, context = {}) {
  const requestId = String(error?.requestId || "");
  const traceId = String(error?.traceId || "");
  const status = Number(error?.status || 0);
  const code = String(error?.code || "");
  const safeError = new Error(
    `FLASHIN API error status=${status || "network"} code=${code || "unspecified"}`,
  );
  safeError.name = "FlashinApiError";

  Sentry.withScope((scope) => {
    scope.setTag("request_id", requestId || "missing");
    scope.setTag("trace_id", traceId || "missing");
    scope.setTag("api_status", String(status || "network"));
    if (code) scope.setTag("api_code", code);
    if (context.action) scope.setTag("action", String(context.action));
    Sentry.captureException(safeError);
  });
}

export function captureUiError(error, component = "unknown") {
  const safeError = new Error(`FLASHIN UI error in ${String(component)}`);
  safeError.name = error?.name || "FlashinUiError";
  Sentry.captureException(safeError);
}

export const frontendReleaseSha = RELEASE_SHA;
