#!/usr/bin/env python3
"""Static integrity checks for critical FLASHIN runtime connections."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


MUTATION_METHODS = {"post", "put", "patch", "delete"}
CRITICAL_ROUTERS = {
    "auth_router",
    "products_router",
    "cart_router",
    "cart_items_router",
    "orders_router",
    "payments_router",
    "returns_router",
    "admin_auth_router",
    "admin_router",
    "fulfillment_router",
    "moysklad_router",
    "support_router",
}
FRONTEND_CORE_API = {
    "telegramAuth",
    "listProducts",
    "getProduct",
    "getCart",
    "addToCart",
    "updateCartItem",
    "removeCartItem",
    "checkout",
    "listOrders",
    "getOrder",
    "cancelOrder",
    "createPayment",
    "createReturn",
}
DEMO_MARKERS = re.compile(r"\b(?:TODO|FIXME|HACK|demo|mock|stub|placeholder)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    message: str
    path: str = ""


def _read(path: Path, findings: list[Finding], check: str) -> str:
    if not path.is_file():
        findings.append(Finding("error", check, "Required file is missing", str(path)))
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        findings.append(Finding("error", check, "File is not valid UTF-8", str(path)))
        return ""


def _parse(path: Path, findings: list[Finding], check: str) -> ast.Module | None:
    source = _read(path, findings, check)
    if not source:
        return None
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        findings.append(
            Finding("error", check, f"Python syntax error at line {exc.lineno}: {exc.msg}", str(path))
        )
        return None


def _decorator_route(decorator: ast.expr) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
        return None
    method = decorator.func.attr.lower()
    if method not in {"get", "post", "put", "patch", "delete", "options", "head"}:
        return None
    path = ""
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        path = str(decorator.args[0].value)
    return method, path


def check_router_registration(root: Path, findings: list[Finding]) -> None:
    path = root / "backend/main.py"
    tree = _parse(path, findings, "router_registration")
    if tree is None:
        return

    included: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "include_router" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            included.append(first.id)

    counts = Counter(included)
    for name in sorted(CRITICAL_ROUTERS):
        count = counts.get(name, 0)
        if count == 0:
            findings.append(Finding("error", "router_registration", f"{name} is not included", str(path)))
        elif count > 1:
            findings.append(
                Finding("error", "router_registration", f"{name} is included {count} times", str(path))
            )


def check_public_catalog_read_only(root: Path, findings: list[Finding]) -> None:
    path = root / "backend/api/products.py"
    tree = _parse(path, findings, "public_catalog_read_only")
    if tree is None:
        return

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = _decorator_route(decorator)
            if route and route[0] in MUTATION_METHODS:
                findings.append(
                    Finding(
                        "error",
                        "public_catalog_read_only",
                        f"Public catalog exposes {route[0].upper()} {route[1] or '/'} via {node.name}",
                        str(path),
                    )
                )

    source = path.read_text(encoding="utf-8")
    for unsupported in ("\"supports_gift_order\": True", "\"supports_telegram_stars\": True"):
        if unsupported in source:
            findings.append(
                Finding(
                    "error",
                    "public_catalog_capabilities",
                    f"Catalog advertises an unimplemented capability: {unsupported}",
                    str(path),
                )
            )


def check_admin_product_write(root: Path, findings: list[Finding]) -> None:
    path = root / "backend/api/admin.py"
    tree = _parse(path, findings, "admin_product_write")
    if tree is None:
        return

    source = path.read_text(encoding="utf-8")
    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_decorator_route(item) == ("post", "/products") for item in node.decorator_list):
            target = node
            break

    if target is None:
        findings.append(Finding("error", "admin_product_write", "Admin product create route is missing", str(path)))
        return

    segment = ast.get_source_segment(source, target) or ""
    if "Depends(get_current_admin)" not in segment:
        findings.append(
            Finding("error", "admin_product_write", "Admin product create route has no admin dependency", str(path))
        )
    if not re.search(r"require_permission\s*\([^\n]*[\"']products\.write[\"']", segment):
        findings.append(
            Finding("error", "admin_product_write", "Admin product create route lacks products.write permission", str(path))
        )


def check_python_duplicates(root: Path, findings: list[Finding]) -> None:
    api_dir = root / "backend/api"
    if not api_dir.is_dir():
        findings.append(Finding("error", "python_duplicates", "backend/api directory is missing", str(api_dir)))
        return

    for path in sorted(api_dir.glob("*.py")):
        tree = _parse(path, findings, "python_duplicates")
        if tree is None:
            continue
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        for name, count in Counter(names).items():
            if count > 1:
                findings.append(
                    Finding("error", "python_duplicates", f"Top-level definition {name!r} occurs {count} times", str(path))
                )

        routes: list[tuple[str, str]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes.extend(route for item in node.decorator_list if (route := _decorator_route(item)))
        for route, count in Counter(routes).items():
            if count > 1:
                findings.append(
                    Finding(
                        "error",
                        "python_duplicates",
                        f"Route {route[0].upper()} {route[1] or '/'} occurs {count} times in one router",
                        str(path),
                    )
                )


def check_frontend_connections(root: Path, findings: list[Finding]) -> None:
    api_path = root / "frontend/src/api.js"
    app_path = root / "frontend/src/App.js"
    api_source = _read(api_path, findings, "frontend_connections")
    app_source = _read(app_path, findings, "frontend_connections")
    if not api_source or not app_source:
        return

    exports = set(re.findall(r"export\s+async\s+function\s+([A-Za-z_$][\w$]*)", api_source))
    import_match = re.search(r"import\s*\{(?P<body>.*?)\}\s*from\s*[\"']\./api[\"']", app_source, re.DOTALL)
    imported = set()
    if import_match:
        imported = {
            part.strip().split(" as ", 1)[0].strip()
            for part in import_match.group("body").split(",")
            if part.strip()
        }
    else:
        findings.append(Finding("error", "frontend_connections", "App.js does not import the API module", str(app_path)))

    for name in sorted(FRONTEND_CORE_API):
        if name not in exports:
            findings.append(Finding("error", "frontend_connections", f"API function {name} is not exported", str(api_path)))
        if name not in imported:
            findings.append(Finding("error", "frontend_connections", f"API function {name} is not wired into App.js", str(app_path)))

    if "await response.json()" in api_source and "return response.text()" in api_source:
        findings.append(
            Finding(
                "warning",
                "frontend_error_handling",
                "Error parsing may try to consume a response body twice",
                str(api_path),
            )
        )


def check_demo_markers(root: Path, findings: list[Finding]) -> None:
    candidates = [
        root / "backend/api",
        root / "backend/services",
        root / "frontend/src",
        root / "admin/src",
        root / "bot",
    ]
    for base in candidates:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in {".py", ".js", ".jsx", ".ts", ".tsx"} or not path.is_file():
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            matches = sorted({match.group(0).lower() for match in DEMO_MARKERS.finditer(source)})
            if matches:
                findings.append(
                    Finding(
                        "warning",
                        "demo_markers",
                        f"Review runtime marker(s): {', '.join(matches)}",
                        str(path),
                    )
                )


def render_markdown(report: dict) -> str:
    lines = [
        "# Integration Integrity Report",
        "",
        f"- Status: **{report['status']}**",
        f"- Errors: **{report['summary']['errors']}**",
        f"- Warnings: **{report['summary']['warnings']}**",
        "",
    ]
    for finding in report["findings"]:
        path = f" — `{finding['path']}`" if finding["path"] else ""
        lines.append(
            f"- **{finding['severity'].upper()}** `{finding['check']}`: {finding['message']}{path}"
        )
    if not report["findings"]:
        lines.append("- No findings.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()

    findings: list[Finding] = []
    check_router_registration(root, findings)
    check_public_catalog_read_only(root, findings)
    check_admin_product_write(root, findings)
    check_python_duplicates(root, findings)
    check_frontend_connections(root, findings)
    check_demo_markers(root, findings)

    errors = sum(item.severity == "error" for item in findings)
    warnings = sum(item.severity == "warning" for item in findings)
    report = {
        "status": "failed" if errors else "ok",
        "summary": {"errors": errors, "warnings": warnings},
        "findings": [asdict(item) for item in findings],
    }

    if args.write_report:
        output_dir = root / "docs/audit"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "integration_integrity.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "integration_integrity.md").write_text(
            render_markdown(report),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
