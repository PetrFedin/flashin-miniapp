function normalizedText(value) {
  return String(value || "").trim();
}

export function validateCheckoutForm(form) {
  const name = normalizedText(form?.name);
  const phone = normalizedText(form?.phone);
  const deliveryType = form?.delivery_type === "courier" ? "courier" : "pickup";
  const address = normalizedText(form?.address);
  const comment = normalizedText(form?.comment);
  const phoneDigits = phone.replace(/\D/g, "");

  if (name.length < 2) return { error: "Укажите имя получателя минимум из двух символов." };
  if (name.length > 120) return { error: "Имя получателя слишком длинное." };
  if (phoneDigits.length < 7 || phoneDigits.length > 15) {
    return { error: "Укажите корректный номер телефона." };
  }
  if (deliveryType === "courier" && address.length < 8) {
    return { error: "Для курьерской доставки укажите полный адрес." };
  }
  if (address.length > 500) return { error: "Адрес слишком длинный." };
  if (comment.length > 1000) return { error: "Комментарий слишком длинный." };

  return {
    value: {
      name,
      phone,
      delivery_type: deliveryType,
      address: deliveryType === "courier" ? address : "",
      comment,
    },
  };
}

export function parseLoyaltyPoints(value) {
  const points = Number(value);
  if (!Number.isFinite(points) || points <= 0) {
    return { error: "Укажите положительное количество баллов." };
  }
  if (!Number.isInteger(points)) {
    return { error: "Баллы списываются целыми значениями." };
  }
  return { value: points };
}

export function validateSizeForm(form) {
  const height = form?.height_cm === "" ? null : Number(form?.height_cm);
  const weight = form?.weight_kg === "" ? null : Number(form?.weight_kg);
  const usualSize = normalizedText(form?.usual_size);

  if (height === null && weight === null && !usualSize) {
    return { error: "Укажите рост, вес или привычный размер." };
  }
  if (height !== null && (!Number.isFinite(height) || height < 100 || height > 230)) {
    return { error: "Рост должен быть от 100 до 230 см." };
  }
  if (weight !== null && (!Number.isFinite(weight) || weight < 30 || weight > 250)) {
    return { error: "Вес должен быть от 30 до 250 кг." };
  }
  if (usualSize.length > 32) return { error: "Привычный размер слишком длинный." };

  return {
    value: {
      height_cm: height,
      weight_kg: weight,
      usual_size: usualSize || null,
      fit_preference: ["slim", "regular", "oversize"].includes(form?.fit_preference)
        ? form.fit_preference
        : "regular",
    },
  };
}
