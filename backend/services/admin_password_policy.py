from __future__ import annotations

_COMMON_ADMIN_PASSWORDS = {
    "password",
    "password123",
    "admin",
    "admin123",
    "qwerty123",
    "change-me-now",
}


def validate_admin_password(password: str, email: str = "") -> None:
    """Validate a newly set administrator password.

    The same policy is used for password-reset and first-admin bootstrap so an
    operator-only path cannot create credentials weaker than the authenticated
    security path.
    """

    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("New password must be between 12 and 1024 characters")

    lowered = password.lower()
    if lowered in _COMMON_ADMIN_PASSWORDS:
        raise ValueError("New password is too weak")

    classes = sum(
        (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        )
    )
    if classes < 3:
        raise ValueError("New password must use at least three character classes")

    email_local = (email or "").split("@", 1)[0].strip().lower()
    if len(email_local) >= 4 and email_local in lowered:
        raise ValueError("New password must not contain the email name")
