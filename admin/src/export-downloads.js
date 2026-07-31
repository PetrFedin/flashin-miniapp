import "./export-downloads.css";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const LEGACY_EXPORT_PATH = "/api/admin/orders/export-csv";
const EXPORTS = Object.freeze([
  {
    key: "orders",
    label: "Скачать заказы CSV",
    path: "/api/import-export/admin/export/orders",
    fallback: "flashin_orders.csv",
  },
  {
    key: "products",
    label: "Скачать товары CSV",
    path: "/api/import-export/admin/export/products",
    fallback: "flashin_products.csv",
  },
]);

function parseApiError(error) {
  const raw = String(error?.message || error || "Неизвестная ошибка");
  try {
    const parsed = JSON.parse(raw);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
  } catch {
    // Proxy and network errors can be plain text.
  }
  return raw.slice(0, 1000);
}

function safeFilename(value, fallback) {
  let candidate = String(value || "").trim();
  try {
    candidate = decodeURIComponent(candidate);
  } catch {
    // Keep the original value if a provider sent malformed percent encoding.
  }
  candidate = candidate
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .replace(/[\\/]/g, "_")
    .replace(/^\.+/, "")
    .trim()
    .slice(0, 180);
  if (!candidate || !candidate.toLowerCase().endsWith(".csv")) return fallback;
  return candidate;
}

function filenameFromDisposition(value, fallback) {
  const disposition = String(value || "");
  const encoded = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1];
  if (encoded) return safeFilename(encoded.replace(/^"|"$/g, ""), fallback);
  const quoted = disposition.match(/filename\s*=\s*"([^"]+)"/i)?.[1];
  if (quoted) return safeFilename(quoted, fallback);
  const plain = disposition.match(/filename\s*=\s*([^;]+)/i)?.[1];
  return safeFilename(plain?.replace(/^"|"$/g, ""), fallback);
}

function triggerBrowserDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function downloadCsv(definition) {
  const token = localStorage.getItem("admin_token");
  if (!token) throw new Error("Административная сессия завершена");

  const response = await fetch(`${API}${definition.path}`, {
    method: "POST",
    headers: {
      Accept: "text/csv",
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) throw new Error(await response.text());

  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (!contentType.includes("text/csv")) {
    throw new Error("Сервер вернул неожиданный формат выгрузки");
  }
  const filename = filenameFromDisposition(
    response.headers.get("content-disposition"),
    definition.fallback,
  );
  const blob = await response.blob();
  if (!blob.size) throw new Error("Сервер вернул пустой файл выгрузки");
  triggerBrowserDownload(blob, filename);
  return filename;
}

function setMessage(container, text, kind) {
  const message = container.querySelector(".admin-export-downloads__message");
  message.className = `admin-export-downloads__message admin-export-downloads__message--${kind}`;
  message.setAttribute("role", kind === "error" ? "alert" : "status");
  message.textContent = text;
}

function createExportControls() {
  const container = document.createElement("div");
  container.className = "admin-export-downloads";
  container.setAttribute("data-export-downloads", "ready");

  const actions = document.createElement("div");
  actions.className = "admin-export-downloads__actions";
  const message = document.createElement("span");
  message.className = "admin-export-downloads__message";
  message.setAttribute("aria-live", "polite");

  for (const definition of EXPORTS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = definition.label;
    button.setAttribute("data-export-key", definition.key);
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      const buttons = actions.querySelectorAll("button");
      buttons.forEach((item) => { item.disabled = true; });
      button.textContent = "Формирование…";
      setMessage(container, `Формируется выгрузка «${definition.label}»…`, "progress");
      try {
        const filename = await downloadCsv(definition);
        setMessage(container, `Файл «${filename}» передан браузеру для сохранения.`, "success");
      } catch (error) {
        setMessage(container, parseApiError(error), "error");
      } finally {
        button.textContent = definition.label;
        buttons.forEach((item) => { item.disabled = false; });
      }
    });
    actions.appendChild(button);
  }

  container.append(actions, message);
  return container;
}

function enhanceLegacyExportLink(link) {
  if (!(link instanceof HTMLAnchorElement) || !link.isConnected) return;
  if (link.closest("[data-export-downloads='ready']")) return;
  const controls = createExportControls();
  link.replaceWith(controls);
}

function enhanceAll(root) {
  root
    .querySelectorAll(`a[href*="${LEGACY_EXPORT_PATH}"]`)
    .forEach(enhanceLegacyExportLink);
}

export function installAuthenticatedExportDownloads() {
  const root = document.getElementById("root");
  if (!root) throw new Error("Admin root is missing");
  const observer = new MutationObserver(() => enhanceAll(root));
  observer.observe(root, { childList: true, subtree: true });
  enhanceAll(root);
  return () => observer.disconnect();
}
