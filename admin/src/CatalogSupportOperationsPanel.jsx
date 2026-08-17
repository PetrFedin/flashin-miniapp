import React, { useEffect, useRef, useState } from "react";

import { hasAdminPermission } from "./adminPermissions.js";
import { AdminApiError, adminJson } from "./api.js";
import CatalogIntentOperationsPanel from "./CatalogIntentOperationsPanel.jsx";

export default function CatalogSupportOperationsPanel({ onUnauthorized, session }) {
  const canShowroomRead = hasAdminPermission(session, "showroom.read");
  const canShowroomWrite = hasAdminPermission(session, "showroom.write");
  const canProductsRead = hasAdminPermission(session, "products.read");
  const canProductsWrite = hasAdminPermission(session, "products.write");
  const [appointments, setAppointments] = useState([]);
  const [feedback, setFeedback] = useState([]);
  const [feedbackStatus, setFeedbackStatus] = useState("published");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);

  function handleFailure(actionError) {
    if (actionError instanceof AdminApiError && actionError.status === 401) {
      onUnauthorized?.("Сессия администратора истекла. Войдите снова.");
      return;
    }
    setError(actionError?.message || "Операторская очередь каталога недоступна.");
  }

  async function loadQueues() {
    const requestId = sequence.current + 1;
    sequence.current = requestId;
    setLoading(true);
    setError("");
    try {
      const [nextAppointments, nextFeedback] = await Promise.all([
        canShowroomRead
          ? adminJson("/api/catalog/admin/showroom/appointments")
          : Promise.resolve([]),
        canProductsRead
          ? adminJson(`/api/catalog/admin/feedback?status=${encodeURIComponent(feedbackStatus)}&limit=200`)
          : Promise.resolve([]),
      ]);
      if (sequence.current !== requestId) return;
      setAppointments(Array.isArray(nextAppointments) ? nextAppointments : []);
      setFeedback(Array.isArray(nextFeedback) ? nextFeedback : []);
    } catch (actionError) {
      if (sequence.current === requestId) handleFailure(actionError);
    } finally {
      if (sequence.current === requestId) setLoading(false);
    }
  }

  useEffect(() => {
    loadQueues();
    return () => { sequence.current += 1; };
  }, [canShowroomRead, canProductsRead, feedbackStatus]);

  async function updateAppointment(appointment, status) {
    if (!canShowroomWrite) return;
    setError("");
    try {
      await adminJson(`/api/catalog/admin/showroom/appointments/${appointment.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
        dedupeKey: `catalog-support-showroom:${appointment.id}:${status}`,
      });
      setNotice(`Запись #${appointment.id}: ${status}.`);
      await loadQueues();
    } catch (actionError) {
      handleFailure(actionError);
    }
  }

  async function moderateFeedback(item, status) {
    if (!canProductsWrite) return;
    setError("");
    try {
      await adminJson(`/api/catalog/admin/feedback/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
        dedupeKey: `catalog-feedback:${item.id}:${status}`,
      });
      setNotice(`Отзыв #${item.id}: ${status}.`);
      await loadQueues();
    } catch (actionError) {
      handleFailure(actionError);
    }
  }

  if (!canShowroomRead && !canProductsRead) return null;

  return (
    <>
      <section className="catalog-support-operations" aria-labelledby="catalog-support-title">
        <div className="section-heading">
          <div>
            <h2 id="catalog-support-title">Showroom и обратная связь</h2>
            <p>Отдельная операторская очередь: support работает с визитами без доступа к редактированию товара; merchandising-команда модерирует отзывы.</p>
          </div>
          <button type="button" onClick={loadQueues} disabled={loading}>{loading ? "Обновление…" : "Обновить"}</button>
        </div>

        {error && <div className="error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></div>}
        {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")}>×</button></div>}

        {canShowroomRead && (
          <article className="service-card" aria-label="Showroom appointments queue">
            <div className="service-item-heading"><h3>Записи на примерку</h3><span>{appointments.length}</span></div>
            {!appointments.length && <p>Активных/исторических записей пока нет.</p>}
            <div className="table">
              {appointments.map((appointment) => (
                <div className="row" key={appointment.id}>
                  <b>#{appointment.id}</b>
                  <span>Product #{appointment.product_id}</span>
                  <span>Customer #{appointment.customer_id}</span>
                  <span>{new Date(appointment.starts_at).toLocaleString("ru-RU")}</span>
                  <span>{appointment.duration_minutes} мин.</span>
                  <span>{appointment.status}</span>
                  {appointment.notes && <span>{appointment.notes}</span>}
                  {canShowroomWrite && appointment.status === "requested" && (
                    <button type="button" onClick={() => updateAppointment(appointment, "confirmed")}>Подтвердить визит</button>
                  )}
                  {canShowroomWrite && appointment.status === "confirmed" && (
                    <button type="button" onClick={() => updateAppointment(appointment, "completed")}>Завершить визит</button>
                  )}
                  {canShowroomWrite && !["cancelled", "completed"].includes(appointment.status) && (
                    <button type="button" onClick={() => updateAppointment(appointment, "cancelled")}>Отменить визит</button>
                  )}
                </div>
              ))}
            </div>
          </article>
        )}

        {canProductsRead && (
          <article className="service-card" aria-label="Product feedback moderation queue">
            <div className="service-item-heading">
              <h3>Отзывы и рейтинги</h3>
              <select aria-label="Статус отзывов" value={feedbackStatus} onChange={(event) => setFeedbackStatus(event.target.value)}>
                <option value="published">Опубликованные</option>
                <option value="hidden">Скрытые</option>
              </select>
            </div>
            {!feedback.length && <p>Отзывов в выбранном статусе нет.</p>}
            <div className="table">
              {feedback.map((item) => (
                <div className="row" key={item.id}>
                  <b>#{item.id} · {item.rating} ★</b>
                  <span>Product #{item.product_id} · {item.product_title}</span>
                  <span>{item.comment || "Без комментария"}</span>
                  <span>{item.status}</span>
                  {canProductsWrite && item.status === "published" && (
                    <button type="button" onClick={() => moderateFeedback(item, "hidden")}>Скрыть отзыв</button>
                  )}
                  {canProductsWrite && item.status === "hidden" && (
                    <button type="button" onClick={() => moderateFeedback(item, "published")}>Опубликовать отзыв</button>
                  )}
                </div>
              ))}
            </div>
          </article>
        )}
      </section>

      {canProductsRead && (
        <CatalogIntentOperationsPanel onUnauthorized={onUnauthorized} session={session} />
      )}
    </>
  );
}
