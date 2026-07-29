from collections.abc import Iterable

_SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]
_NUMERIC_SIZE_MAP = {
    "40": "XS",
    "42": "XS",
    "44": "S",
    "46": "M",
    "48": "L",
    "50": "XL",
    "52": "XXL",
    "54": "XXL",
    "56": "XXL",
}
_FIT_ADJUSTMENT = {"slim": -1, "regular": 0, "oversize": 1}


def _normalize_size(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper().replace(" ", "")
    if not normalized:
        return None
    normalized = _NUMERIC_SIZE_MAP.get(normalized, normalized)
    if normalized not in _SIZE_ORDER:
        raise ValueError("Usual size must be XS, S, M, L, XL, XXL or a Russian size from 40 to 56")
    return normalized


def _validate_measurements(height_cm: int | None, weight_kg: int | None) -> None:
    if height_cm is not None and not 140 <= height_cm <= 210:
        raise ValueError("Height must be between 140 and 210 cm")
    if weight_kg is not None and not 40 <= weight_kg <= 180:
        raise ValueError("Weight must be between 40 and 180 kg")


def _estimated_index(height_cm: int | None, weight_kg: int | None) -> int:
    if weight_kg is None:
        raise ValueError("Weight is required when usual size is not provided")

    if weight_kg < 58:
        index = 1
    elif weight_kg < 72:
        index = 2
    elif weight_kg < 87:
        index = 3
    elif weight_kg < 104:
        index = 4
    else:
        index = 5

    if height_cm is not None:
        if height_cm <= 164 and weight_kg < 90:
            index -= 1
        elif height_cm >= 190 and weight_kg >= 72:
            index += 1
    return max(0, min(index, len(_SIZE_ORDER) - 1))


def _nearest_available(target_index: int, available_sizes: Iterable[str] | None) -> tuple[str, bool]:
    target = _SIZE_ORDER[target_index]
    if available_sizes is None:
        return target, True

    normalized_available: set[str] = set()
    for raw_size in available_sizes:
        try:
            normalized = _normalize_size(raw_size)
        except ValueError:
            continue
        if normalized:
            normalized_available.add(normalized)

    if not normalized_available:
        return target, False
    if target in normalized_available:
        return target, True

    nearest = min(
        normalized_available,
        key=lambda size: (abs(_SIZE_ORDER.index(size) - target_index), _SIZE_ORDER.index(size)),
    )
    return nearest, False


def suggest_size(
    height_cm: int | None,
    weight_kg: int | None,
    usual_size: str | None,
    fit_preference: str = "regular",
    available_sizes: Iterable[str] | None = None,
) -> dict:
    _validate_measurements(height_cm, weight_kg)
    normalized_fit = str(fit_preference or "regular").strip().lower()
    if normalized_fit not in _FIT_ADJUSTMENT:
        raise ValueError("Fit preference must be slim, regular or oversize")

    normalized_usual_size = _normalize_size(usual_size)
    basis: list[str] = []
    if normalized_usual_size:
        base_index = _SIZE_ORDER.index(normalized_usual_size)
        confidence = "medium"
        basis.append(f"usual_size:{normalized_usual_size}")
    else:
        base_index = _estimated_index(height_cm, weight_kg)
        confidence = "low"
        basis.append(f"height_cm:{height_cm}" if height_cm is not None else "height:not_provided")
        basis.append(f"weight_kg:{weight_kg}")

    adjusted_index = max(
        0,
        min(base_index + _FIT_ADJUSTMENT[normalized_fit], len(_SIZE_ORDER) - 1),
    )
    basis.append(f"fit:{normalized_fit}")
    suggested_size, exact_available = _nearest_available(adjusted_index, available_sizes)

    if available_sizes is None:
        availability_note = "Наличие конкретного товара не учитывалось."
    elif exact_available:
        availability_note = "Размер присутствует среди доступных вариантов товара."
    else:
        availability_note = (
            "Рассчитанный размер отсутствует: показан ближайший доступный вариант либо базовый размер для подписки на поступление."
        )

    return {
        "suggested_size": suggested_size,
        "base_size": _SIZE_ORDER[adjusted_index],
        "confidence": confidence,
        "exact_available": exact_available,
        "basis": basis,
        "note": (
            f"{availability_note} Рекомендация является ориентиром: "
            "для окончательного выбора необходимо сверить замеры конкретного изделия."
        ),
    }
