import React from 'react';

export default function CartView({ items, onBack, onCheckout, onRemove, t }) {
  const total = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  return (
    <div>
      <h2>{t('cart', 'title')}</h2>
      {items.length === 0 ? (
        <p>{t('cart', 'empty')}</p>
      ) : (
        <>
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {items.map((item, index) => (
              <li key={`${item.variant_id}-${index}`} className="product-card">
                <strong>{item.title}</strong>
                <p>Размер: {item.size}</p>
                <p>{item.price} {item.currency} × {item.quantity}</p>
                <button onClick={() => onRemove(index)}>Удалить</button>
              </li>
            ))}
          </ul>
          <p><strong>{t('cart', 'total')}: {total.toFixed(0)} RUB</strong></p>
          <button onClick={onCheckout}>{t('cart', 'checkout')}</button>
        </>
      )}
      <button onClick={onBack}>{t('cart', 'back_to_catalog')}</button>
    </div>
  );
}
