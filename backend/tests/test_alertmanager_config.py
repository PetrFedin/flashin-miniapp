import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from render_alertmanager_config import (  # noqa: E402
    RUNTIME_CONFIG_MODE,
    RUNTIME_DIRECTORY_MODE,
    _write_atomic,
    load_settings,
    render_config,
)


def _secret_file(tmp_path: Path, *, mode: int = 0o600) -> Path:
    path = tmp_path / "alertmanager.env"
    path.write_text(
        "ALERTMANAGER_WEBHOOK_URL=https://alerts.flashin-pilot.net/receiver\n"
        "ALERTMANAGER_ONCALL_OWNER=pilot-primary\n"
        "ALERTMANAGER_SEND_RESOLVED=true\n",
        encoding="utf-8",
    )
    os.chmod(path, mode)
    return path


def test_alertmanager_secret_input_is_strict_and_renders_webhook(tmp_path):
    secret = _secret_file(tmp_path)

    settings = load_settings(secret)
    rendered = render_config(settings)

    assert settings.oncall_owner == "pilot-primary"
    assert settings.send_resolved is True
    assert "https://alerts.flashin-pilot.net/receiver" in rendered
    assert "FlashinPilotAlertDeliverySmoke" in rendered
    assert "send_resolved: true" in rendered


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions are the production contract")
def test_alertmanager_secret_input_rejects_group_or_world_access(tmp_path):
    secret = _secret_file(tmp_path, mode=0o640)

    with pytest.raises(ValueError, match="0600 or stricter"):
        load_settings(secret)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions are the production contract")
def test_generated_config_is_container_readable_inside_private_runtime_directory(tmp_path):
    target = tmp_path / "runtime" / "alertmanager.yml"

    _write_atomic(target, "global:\n  resolve_timeout: 5m\n")

    assert stat.S_IMODE(target.parent.stat().st_mode) == RUNTIME_DIRECTORY_MODE == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == RUNTIME_CONFIG_MODE == 0o644
    assert target.read_text(encoding="utf-8").startswith("global:")
