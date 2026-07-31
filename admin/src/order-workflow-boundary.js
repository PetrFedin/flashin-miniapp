import "./order-workflow-boundary.css";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const ENHANCED_ATTRIBUTE = "data-workflow-boundary";

function parseError(error) {
  const raw = String(error?.message || error || "Неизвестная ошибка");
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
  } catch {
    // Proxy and network failures may be plain text.
  }
  return raw.slice(0, 1000);
}

async function cancelSafely(orderId) {
  const token = localStorage.getItem("admin_token");
  if (!token) throw new Error("Административная сессия завершена");
  const response = await fetch(`${API}/api/admin/orders/${orderId}/cancel-safe`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function setMessage(row, text, kind) {
  let message = row.querySelector(".order-workflow-message");
  if (!message) {
    message = document.createElement("span");
    message.className = "order-workflow-message";
    message.setAttribute("role", kind === "error" ? "alert" : "status");
    row.appendChild(message);
  }
  message.className = `order-workflow-message order-workflow-message--${kind}`;
  message.textContent = text;
}

function orderIdFromRow(row) {
  const raw = row.querySelector("b")?.textContent || "";
  const value = Number(raw.replace(/\D/g, ""));
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function enhanceOrderRow(row) {
  row.querySelectorAll("select").forEach((select) => select.remove());

  const spans = row.querySelectorAll(":scope > span");
  const orderId = orderIdFromRow(row);
  const status = spans[0]?.textContent?.trim() || "";
  const paymentStatus = spans[1]?.textContent?.trim() || "";
  const eligible = orderId && status === "created" && paymentStatus === "pending";
  const existing = row.querySelector(".order-safe-cancel");

  if (!eligible) {
    existing?.remove();
    row.setAttribute(ENHANCED_ATTRIBUTE, "read-only");
    return;
  }
  if (existing) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "order-safe-cancel";
  button.textContent = "Безопасно отменить";
  button.addEventListener("click", async () => {
    if (button.disabled) return;
    const confirmed = window.confirm(
      `Отменить заказ #${orderId} до начала оплаты? Резерв, промокод и loyalty hold будут освобождены.`,
    );
    if (!confirmed) return;

    button.disabled = true;
    button.textContent = "Отмена…";
    setMessage(row, "Выполняется безопасная отмена заказа…", "progress");
    try {
      const order = await cancelSafely(orderId);
      if (spans[0]) spans[0].textContent = order.status;
      if (spans[1]) spans[1].textContent = order.payment_status;
      button.remove();
      row.setAttribute(ENHANCED_ATTRIBUTE, "cancelled");
      setMessage(row, `Заказ #${orderId} отменён безопасным workflow.`, "success");
    } catch (error) {
      button.disabled = false;
      button.textContent = "Безопасно отменить";
      setMessage(row, parseError(error), "error");
    }
  });
  row.appendChild(button);
  row.setAttribute(ENHANCED_ATTRIBUTE, "safe-cancel");
}

function enhanceAll(root) {
  root.querySelectorAll(".row.order").forEach(enhanceOrderRow);
}

export function installOrderWorkflowBoundary() {
  const root = document.getElementById("root");
  if (!root) throw new Error("Admin root is missing");

  const observer = new MutationObserver(() => enhanceAll(root));
  observer.observe(root, { childList: true, subtree: true });
  enhanceAll(root);
  return () => observer.disconnect();
}
