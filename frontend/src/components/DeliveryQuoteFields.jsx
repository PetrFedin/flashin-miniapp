import React from "react";

function money(value, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function expiryLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "ограниченное время";
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

export default function DeliveryQuoteFields({
  address,
  quote,
  status,
  disabled = false,
  onAddressChange,
  onQuote,
}) {
  const loading = status === "loading";
  const ready = status === "ready" && quote;
  const stale = status === "stale";

  return (
    <section className="delivery-quote-section" aria-live="polite">
      <div className="delivery-address-grid">
        <label>
          Город
          <input
            autoComplete="address-level2"
            placeholder="Москва"
            value={address.city}
            onChange={(event) => onAddressChange("city", event.target.value)}
            disabled={disabled || loading}
          />
        </label>
        <label>
          Индекс
          <input
            autoComplete="postal-code"
            inputMode="numeric"
            placeholder="101000"
            value={address.postal_code}
            onChange={(event) => onAddressChange("postal_code", event.target.value)}
            disabled={disabled || loading}
          />
        </label>
      </div>
      <label>
        Регион
        <input
          autoComplete="address-level1"
          placeholder="Необязательно"
          value={address.region}
          onChange={(event) => onAddressChange("region", event.target.value)}
          disabled={disabled || loading}
        />
      </label>
      <label>
        Улица, дом, квартира
        <textarea
          autoComplete="street-address"
          placeholder="Тверская улица, дом 1, квартира 10"
          value={address.address_line}
          onChange={(event) => onAddressChange("address_line", event.target.value)}
          disabled={disabled || loading}
        />
      </label>

      <div className={`delivery-quote-card ${ready ? "ready" : stale ? "stale" : ""}`}>
        <div className="delivery-quote-heading">
          <div>
            <span className="meta">Стоимость доставки</span>
            <strong>{ready ? money(quote.price, quote.currency) : "Рассчитаем по адресу"}</strong>
          </div>
          {ready && <span className="status success">Подтверждено</span>}
          {stale && <span className="status">Нужно обновить</span>}
        </div>
        {ready ? (
          <>
            <p>{quote.address_snapshot}</p>
            <div className="delivery-quote-meta">
              <span>{quote.service_code}</span>
              <span>до {expiryLabel(quote.expires_at)}</span>
            </div>
          </>
        ) : (
          <p>{stale ? "Условия доставки изменились. Получите новый расчёт перед оформлением." : "Цена и доступность фиксируются отдельной котировкой до создания заказа."}</p>
        )}
        <button
          type="button"
          className="secondary"
          onClick={onQuote}
          disabled={disabled || loading}
        >
          {loading ? "Проверяем адрес…" : ready ? "Пересчитать доставку" : "Рассчитать доставку"}
        </button>
      </div>
    </section>
  );
}
