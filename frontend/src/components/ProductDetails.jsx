import React, { useState } from 'react';

export default function ProductDetails({ product, onAdd, onBack, t }) {
  const [variantId, setVariantId] = useState(product?.variants?.[0]?.id || '');
  if (!product) return null;

  const selectedVariant = product.variants?.find((variant) => String(variant.id) === String(variantId));
  const canAdd = selectedVariant && selectedVariant.available_qty > 0;

  return (
    <div>
      <button onClick={onBack}>{t('product', 'back')}</button>
      {product.image_url ? (
        <img className="product-image" src={product.image_url} alt={product.title} loading="lazy" />
      ) : (
        <div className="product-image" />
      )}
      <h2>{product.title}</h2>
      <p>{product.brand}</p>
      <p>{product.description || ''}</p>
      <p>{t('catalog', 'price')}: {product.price} {product.currency}</p>

      <label>
        Размер
        <select value={variantId} onChange={(event) => setVariantId(event.target.value)}>
          {product.variants?.map((variant) => (
            <option key={variant.id} value={variant.id} disabled={variant.available_qty <= 0}>
              {variant.size} {variant.color ? ` / ${variant.color}` : ''} — {variant.available_qty > 0 ? `в наличии: ${variant.available_qty}` : 'нет в наличии'}
            </option>
          ))}
        </select>
      </label>

      <button disabled={!canAdd} onClick={() => onAdd(product, selectedVariant)}>
        {canAdd ? t('product', 'add_to_cart') : 'Нет в наличии'}
      </button>
    </div>
  );
}
