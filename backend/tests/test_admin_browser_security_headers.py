from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "deploy" / "Caddyfile"


def _admin_site_block() -> str:
    source = CADDYFILE.read_text(encoding="utf-8")
    return source.split("admin.flashin.store {", 1)[1].split("api.flashin.store {", 1)[0]


def test_admin_origin_has_fail_closed_browser_security_boundary():
    block = _admin_site_block()

    assert 'X-Frame-Options "DENY"' in block
    assert 'Cache-Control "no-store"' in block
    assert 'Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()"' in block
    assert 'Cross-Origin-Opener-Policy "same-origin"' in block
    assert 'Cross-Origin-Resource-Policy "same-origin"' in block

    csp_line = next(
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("Content-Security-Policy ")
    )
    required_directives = (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self' https://api.flashin.store",
        "manifest-src 'self'",
        "worker-src 'self' blob:",
        "upgrade-insecure-requests",
    )
    for directive in required_directives:
        assert directive in csp_line

    assert "'unsafe-inline'" not in csp_line
    assert "'unsafe-eval'" not in csp_line
    assert " *" not in csp_line


def test_customer_and_api_origins_do_not_inherit_admin_only_csp():
    source = CADDYFILE.read_text(encoding="utf-8")
    mini_block = source.split("mini.flashin.store {", 1)[1].split("admin.flashin.store {", 1)[0]
    api_block = source.split("api.flashin.store {", 1)[1]

    assert "Content-Security-Policy" not in mini_block
    assert "Content-Security-Policy" not in api_block
