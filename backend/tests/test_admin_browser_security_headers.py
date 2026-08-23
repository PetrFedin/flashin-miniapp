import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "deploy" / "Caddyfile"
ADMIN_ROOT = ROOT / "admin"


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
        "frame-src 'none'",
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


def test_admin_source_stays_compatible_with_strict_script_and_style_csp():
    index_source = (ADMIN_ROOT / "index.html").read_text(encoding="utf-8")
    assert "<style" not in index_source.lower()
    assert not re.search(r"<script(?![^>]*\bsrc\s*=)[^>]*>", index_source, flags=re.IGNORECASE)

    source_files = sorted((ADMIN_ROOT / "src").glob("*.js")) + sorted(
        (ADMIN_ROOT / "src").glob("*.jsx")
    )
    assert source_files
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\bstyle\s*=\s*\{", source), f"Inline style in {path.name} would require unsafe-inline"
        assert "dangerouslySetInnerHTML" not in source, f"Raw HTML sink present in {path.name}"


def test_customer_and_api_origins_do_not_inherit_admin_only_csp():
    source = CADDYFILE.read_text(encoding="utf-8")
    mini_block = source.split("mini.flashin.store {", 1)[1].split("admin.flashin.store {", 1)[0]
    api_block = source.split("api.flashin.store {", 1)[1]

    assert "Content-Security-Policy" not in mini_block
    assert "Content-Security-Policy" not in api_block
