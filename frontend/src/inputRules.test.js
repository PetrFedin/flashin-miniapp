import assert from "node:assert/strict";
import test from "node:test";

import { parseLoyaltyPoints, validateCheckoutForm, validateSizeForm } from "./inputRules.js";

test("checkout normalizes valid recipient data", () => {
  assert.deepEqual(
    validateCheckoutForm({
      name: "  Пётр  ",
      phone: "+46 70 123 45 67",
      delivery_type: "courier",
      address: "  Stockholm, Example 10  ",
      comment: "  Позвонить заранее  ",
    }),
    {
      value: {
        name: "Пётр",
        phone: "+46 70 123 45 67",
        delivery_type: "courier",
        address: "Stockholm, Example 10",
        comment: "Позвонить заранее",
      },
    },
  );
});

test("checkout rejects invalid phone and incomplete courier address", () => {
  assert.match(validateCheckoutForm({ name: "Пётр", phone: "123" }).error, /телефон/i);
  assert.match(
    validateCheckoutForm({
      name: "Пётр",
      phone: "+46 701234567",
      delivery_type: "courier",
      address: "дом",
    }).error,
    /адрес/i,
  );
});

test("loyalty points must be positive integers", () => {
  assert.deepEqual(parseLoyaltyPoints("25"), { value: 25 });
  assert.match(parseLoyaltyPoints("0").error, /положительное/i);
  assert.match(parseLoyaltyPoints("1.5").error, /целыми/i);
  assert.match(parseLoyaltyPoints("abc").error, /положительное/i);
});

test("size helper validates measurement ranges", () => {
  assert.deepEqual(
    validateSizeForm({ height_cm: "182", weight_kg: "78", usual_size: "L", fit_preference: "regular" }),
    {
      value: {
        height_cm: 182,
        weight_kg: 78,
        usual_size: "L",
        fit_preference: "regular",
      },
    },
  );
  assert.match(validateSizeForm({ height_cm: "90", weight_kg: "", usual_size: "" }).error, /рост/i);
  assert.match(validateSizeForm({ height_cm: "", weight_kg: "", usual_size: "" }).error, /укажите/i);
});
