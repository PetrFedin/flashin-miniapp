import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import AdminRuntimeStatus from "./AdminRuntimeStatus";
import ScheduledJobsPanel from "./ScheduledJobsPanel";
import { installAdminActionCoordinator } from "./admin-action-coordinator";
import { installAdminDataCoordinator } from "./admin-data-coordinator";
import { installAuthenticatedExportDownloads } from "./export-downloads";
import { installOrderWorkflowBoundary } from "./order-workflow-boundary";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function readAdminToken() {
  return localStorage.getItem("admin_token") || "";
}

function ScheduledJobsRoot({ sessionEvent }) {
  const [token, setToken] = useState(readAdminToken);

  useEffect(() => {
    let current = readAdminToken();
    const refresh = () => {
      const next = readAdminToken();
      if (next !== current) {
        current = next;
        setToken(next);
      }
    };
    const expire = () => {
      current = "";
      setToken("");
    };
    const timer = window.setInterval(refresh, 500);
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener(sessionEvent, expire);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("storage", refresh);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(sessionEvent, expire);
    };
  }, [sessionEvent]);

  const api = useCallback(async (path, options = {}) => {
    const currentToken = readAdminToken();
    if (!currentToken) throw new Error("Административная сессия завершена");
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${currentToken}`,
        ...(options.headers || {}),
      },
    });
    if (!response.ok) throw new Error(await response.text());
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return response.json();
    return response.text();
  }, []);

  if (!token) return null;
  return <>
    <AdminRuntimeStatus key={`runtime:${token}`} />
    <main className="scheduled-jobs-host">
      <section>
        <ScheduledJobsPanel key={`jobs:${token}`} api={api} />
      </section>
    </main>
  </>;
}

async function bootstrap() {
  const dataCoordinator = installAdminDataCoordinator();
  const actionCoordinator = installAdminActionCoordinator();
  installOrderWorkflowBoundary();
  installAuthenticatedExportDownloads();

  // Both coordinators are installed before legacy handlers are registered.
  // GET reads are parallelized by the data coordinator; mutations are fenced
  // by the action coordinator and then delegated through the data layer.
  await import("./main.jsx");

  const root = document.getElementById("scheduled-jobs-root");
  if (!root) throw new Error("Scheduled jobs root is missing");
  createRoot(root).render(
    <ScheduledJobsRoot
      sessionEvent={actionCoordinator.sessionEvent || dataCoordinator.sessionEvent}
    />,
  );
}

bootstrap().catch((error) => {
  console.error("FLASHIN Admin bootstrap failed", error);
  const root = document.getElementById("scheduled-jobs-root");
  if (root) {
    root.textContent = "Административная панель не запущена. Обновите страницу.";
    root.setAttribute("role", "alert");
  }
});
