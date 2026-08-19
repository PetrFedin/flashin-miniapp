from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ANY_ACTION = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
PINNED_ACTION_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")

CORE_JOBS = (
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
)


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_all_github_actions_are_pinned_to_immutable_commit_sha():
    workflow_paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert workflow_paths

    violations: list[str] = []
    for path in workflow_paths:
        body = path.read_text(encoding="utf-8")
        for match in ANY_ACTION.finditer(body):
            action_ref = match.group(1)
            if action_ref.startswith("./"):
                continue
            if not PINNED_ACTION_REF.fullmatch(action_ref):
                violations.append(f"{path.relative_to(ROOT)}: {action_ref}")

    assert violations == [], "Floating GitHub Action refs are forbidden: " + "; ".join(violations)


def test_core_ci_required_job_names_are_unchanged():
    ci = _text(".github/workflows/ci.yml")
    for job in CORE_JOBS:
        assert re.search(rf"^  {re.escape(job)}:\s*$", ci, re.MULTILINE), job


def test_dependabot_covers_all_runtime_dependency_ecosystems():
    config = _text(".github/dependabot.yml")
    assert config.count("package-ecosystem: pip") == 2
    assert config.count("package-ecosystem: npm") == 3
    assert "package-ecosystem: github-actions" in config
    assert "package-ecosystem: docker" in config
    for directory in ("/backend", "/bot", "/frontend", "/admin", "/e2e"):
        assert f"directory: {directory}" in config


def test_security_workflow_has_required_supply_chain_gates():
    security = _text(".github/workflows/security.yml")

    for job in (
        "dependency-review",
        "codeql",
        "secret-scan",
        "dependency-vulnerability-scan",
        "image-security",
    ):
        assert re.search(rf"^  {re.escape(job)}:\s*$", security, re.MULTILINE), job

    assert "actions/dependency-review-action@" in security
    assert "github/codeql-action/init@" in security
    assert "github/codeql-action/analyze@" in security
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


def test_ingress_builds_exact_caddy_source_with_patched_go_dependencies():
    ingress = _text("Dockerfile.ingress")
    compose = _text("docker-compose.yml")

    assert "FROM golang:1.26.7-alpine3.24 AS build" in ingress
    assert "CADDY_COMMIT=e2eee6a7fce366321294c9c2a79f3146891dcbdf" in ingress
    assert "golang.org/x/net@v0.56.0" in ingress
    assert "golang.org/x/text@v0.39.0" in ingress
    assert "google.golang.org/grpc@v1.82.1" in ingress
    assert "FROM alpine:3.24.1" in ingress
    assert "dockerfile: Dockerfile.ingress" in compose
    assert "image: caddy:2" not in compose


def test_release_evidence_contains_sbom_and_longer_retention():
    release = _text(".github/workflows/release.yml")
    assert "flashin-source-sbom.cdx.json" in release
    assert "format: cyclonedx" in release
    assert "retention-days: 90" in release
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in release
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in release
