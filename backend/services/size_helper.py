def suggest_size(height_cm: int | None, weight_kg: int | None, usual_size: str | None, fit_preference: str = "regular") -> dict:
    if usual_size:
        base = usual_size.upper()
    elif height_cm and weight_kg:
        if weight_kg < 60:
            base = "S"
        elif weight_kg < 78:
            base = "M"
        elif weight_kg < 92:
            base = "L"
        else:
            base = "XL"
    else:
        base = "M"

    order = ["XS", "S", "M", "L", "XL", "XXL"]
    idx = order.index(base) if base in order else 2
    if fit_preference == "oversize":
        idx = min(idx + 1, len(order) - 1)
    if fit_preference == "slim":
        idx = max(idx - 1, 0)

    return {"suggested_size": order[idx], "confidence": "medium", "note": "Use as helper only; confirm with garment measurements."}
