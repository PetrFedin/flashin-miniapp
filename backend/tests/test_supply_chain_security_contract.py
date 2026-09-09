import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_security_workflow_uses_sha_pinned_actions_and_scans_every_release_image():
    security = _text(".github/workflows/security.yml")

    # Assert the actions the Security workflow actually executes. Do not require
    # setup-python/setup-node when the workflow has no runtime setup step; the
    # supply-chain contract is that every external action reference is immutable.
    for action in (
        "actions/checkout@",
        "actions/dependency-review-action@",
        "github/codeql-action/init@",
        "github/codeql-action/analyze@",
        "aquasecurity/trivy-action@",
        "actions/upload-artifact@",
    ):
        assert action in security

    action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", security, flags=re.MULTILINE)
    assert action_refs
    for ref in action_refs:
        if ref.startswith("./"):
            continue
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref), f"Security action is not SHA-pinned: {ref}"

    assert "scanners: secret" in security
    assert "scanners: vuln" in security
    assert "severity: HIGH,CRITICAL" in security
    assert "format: cyclonedx" in security
    for image in ("backend", "bot", "frontend", "admin", "ingress"):
        assert f"- name: {image}" in security
        assert f"dockerfile: Dockerfile.{image}" in security


def test_static_runtimes_do_not_ship_the_vulnerable_caddy_binary():
    frontend = _text("Dockerfile.frontend")
    admin = _text("Dockerfile.admin")
    for dockerfile in (frontend, admin):
        assert "FROM alpine:3.24.1" in dockerfile
        assert "busybox" in dockerfile
        assert "FROM caddy:" not in dockerfile
        assert "USER flashin" in dockerfile


def test_ingress_builds_versioned_caddy_with_patched_go_dependencies():
    ingress = _text("Dockerfile.ingress")
    compose = _text("docker-compose.yml")

    assert "FROM golang:1.26.7-alpine3.24 AS build" in ingress
    assert "CADDY_VERSION=2.11.4" in ingress
    assert "GOSUMDB=sum.golang.org" in ingress
    assert 'github.com/caddyserver/caddy/v2@v${CADDY_VERSION}' in ingress
    assert "golang.org/x/crypto@v0.55.0" in ingress
    assert "golang.org/x/net@v0.58.0" in ingress
    assert "golang.org/x/text@v0.41.0" in ingress
    assert "google.golang.org/grpc@v1.83.2" in ingress
    assert "go mod verify" in ingress
    assert "go version -m /out/caddy" in ingress
    assert "golang.org/x/crypto[[:space:]]+v0\\.55\\.0" in ingress
    assert "golang.org/x/net[[:space:]]+v0\\.58\\.0" in ingress
    assert "golang.org/x/text[[:space:]]+v0\\.41\\.0" in ingress
    assert "google.golang.org/grpc[[:space:]]+v1\\.83\\.2" in ingress
    assert "golang.org/x/net@v0.57.0" not in ingress
    assert "google.golang.org/grpc@v1.83.1" not in ingress
    assert "FROM alpine:3.24.1" in ingress
    assert "dockerfile: Dockerfile.ingress" in compose
    assert "image: caddy:2" not in compose


def test_bot_runtime_uses_pinned_safe_http_stack():
    bot_dockerfile = _text("Dockerfile.bot")
    bot_requirements = _text("bot/requirements.txt")

    assert "COPY bot/requirements.txt /app/bot/requirements.txt" in bot_dockerfile
    assert "aiogram==3.30.0" in bot_requirements
    assert "aiohttp==3.14.3" in bot_requirements
    assert "aiogram==3.17.0" not in bot_dockerfile


def test_release_evidence_contains_sbom_and_longer_retention():
    release = _text(".github/workflows/release.yml")
    assert "flashin-source-sbom.cdx.json" in release
    assert "retention-days: 30" in release
