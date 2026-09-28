from scripts import preflight


def _env_text(*, app_env: str, admin_password: str | None = None) -> str:
    values = {
        "APP_ENV": app_env,
        "DATABASE_URL": "postgresql+psycopg2://flashin:password@db:5432/flashin",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "JWT_SECRET": "j" * 48,
        "ADMIN_EMAIL": "owner@flashin.store",
        "MINI_APP_URL": "https://mini.flashin.store",
        "API_PUBLIC_URL": "https://api.flashin.store",
    }
    if admin_password is not None:
        values["ADMIN_PASSWORD"] = admin_password
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _run_contract(monkeypatch, tmp_path, text: str) -> list[str]:
    monkeypatch.setattr(preflight, "REQUIRED_FILES", [])
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    return preflight.run_preflight(tmp_path)


def test_production_preflight_accepts_env_without_admin_password(monkeypatch, tmp_path):
    errors = _run_contract(
        monkeypatch,
        tmp_path,
        _env_text(app_env="production"),
    )

    assert errors == []


def test_production_preflight_rejects_persisted_admin_password(monkeypatch, tmp_path):
    errors = _run_contract(
        monkeypatch,
        tmp_path,
        _env_text(
            app_env="production",
            admin_password="Correct-Horse-2026!",
        ),
    )

    assert errors == [
        "ADMIN_PASSWORD must not be stored in production; "
        "use the interactive first-admin bootstrap"
    ]


def test_nonproduction_preflight_still_requires_admin_password(monkeypatch, tmp_path):
    errors = _run_contract(
        monkeypatch,
        tmp_path,
        _env_text(app_env="development"),
    )

    assert errors == ["Missing .env keys: ADMIN_PASSWORD"]


def test_nonproduction_preflight_accepts_explicit_local_admin_password(monkeypatch, tmp_path):
    errors = _run_contract(
        monkeypatch,
        tmp_path,
        _env_text(
            app_env="development",
            admin_password="Correct-Horse-2026!",
        ),
    )

    assert errors == []
