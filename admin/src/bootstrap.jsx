import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import ScheduledJobsPanel from "./ScheduledJobsPanel";
import { installOrderWorkflowBoundary } from "./order-workflow-boundary";
import "./main.jsx";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function readAdminToken() {
  return localStorage.getItem("admin_token") || "";
}

function ScheduledJobsRoot() {
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
    const timer = window.setInterval(refresh, 500);
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("storage", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);

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
  return <main className="scheduled-jobs-host">
    <section>
      <ScheduledJobsPanel key={token} api={api} />
    </section>
  </main>;
}

installOrderWorkflowBoundary();

const root = document.getElementById("scheduled-jobs-root");
if (!root) throw new Error("Scheduled jobs root is missing");
createRoot(root).render(<ScheduledJobsRoot />);
