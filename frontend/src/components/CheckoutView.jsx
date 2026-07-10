import React, { useState } from 'react';

export default function CheckoutView({ cart, user, onBack, onSubmit, error, loading }) {
  const [form, setForm] = useState({
    name: user?.first_name || '',
    phone: '',
    delivery_type: 'pickup',
    address: '',
    comment: ''
  });

  const update = (key, value) => setForm((previous) => ({ ...previous, [key]: value }));

  return (
    <div>
      <button onClick={onBack}>Назад</button>
      <h2>Оформление заказа</h2>
      {error && <div className="error-box">{error}</div>}
      <input placeholder="Имя" value={form.name} onChange={(event) => update('name', event.target.value)} />
      <input placeholder="Телефон" value={form.phone} onChange={(event) => update('phone', event.target.value)} />
      <select value={form.delivery_type} onChange={(event) => update('delivery_type', event.target.value)}>
        <option value="pickup">Самовывоз</option>
        <option value="courier">Курьер</option>
        <option value="cdek">СДЭК</option>
        <option value="fitting">Примерка в шоуруме</option>
      </select>
      {form.delivery_type !== 'pickup' && form.delivery_type !== 'fitting' && (
        <textarea placeholder="Адрес доставки" value={form.address} onChange={(event) => update('address', event.target.value)} />
      )}
      <textarea placeholder="Комментарий" value={form.comment} onChange={(event) => update('comment', event.target.value)} />
      <button disabled={loading || cart.length === 0} onClick={() => onSubmit(form)}>
        {loading ? 'Создаём заказ...' : 'Создать заказ и перейти к оплате'}
      </button>
    </div>
  );
}
