from pathlib import Path

from fastapi import FastAPI

from backend.api import catalog_merchandising, catalog_showroom


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHOWROOM_OPERATIONS = {
    ("/catalog/showroom/appointments", "POST"),
    ("/catalog/showroom/appointments/me", "GET"),
    ("/catalog/admin/showroom/appointments", "GET"),
    ("/catalog/admin/showroom/appointments/{appointment_id}", "PATCH"),
}


def _operations(routes) -> list[tuple[str, str]]:
    return [
        (route.path, method)
        for route in routes
        if getattr(route, "path", None) and getattr(route, "methods", None)
        for method in route.methods
        if method in {"GET", "POST", "PATCH", "PUT", "DELETE"}
    ]


def test_showroom_routes_have_one_physical_owner():
    merchandising_operations = set(_operations(catalog_merchandising.router.routes))
    canonical_operations = set(_operations(catalog_showroom.router.routes))

    assert merchandising_operations.isdisjoint(EXPECTED_SHOWROOM_OPERATIONS)
    assert canonical_operations == EXPECTED_SHOWROOM_OPERATIONS


def test_application_composition_exposes_canonical_showroom_contract():
    # FastAPI 0.137+ preserves included routers as a route tree instead of
    # flattening app.routes. OpenAPI is the supported composed contract; physical
    # uniqueness is asserted above and main.py inclusion uniqueness below.
    application = FastAPI()
    application.include_router(catalog_merchandising.router, prefix="/api")
    application.include_router(catalog_showroom.router, prefix="/api")

    openapi = application.openapi()["paths"]
    for path, method in EXPECTED_SHOWROOM_OPERATIONS:
        public_path = f"/api{path}"
        assert method.lower() in openapi[public_path], (public_path, method, openapi)


def test_main_registers_canonical_showroom_without_runtime_route_surgery():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert main_source.count(
        'app.include_router(catalog_merchandising_router, prefix="/api")'
    ) == 1
    assert main_source.count(
        'app.include_router(catalog_showroom_router, prefix="/api")'
    ) == 1
    assert "_REPLACED_CATALOG_SHOWROOM_ROUTES" not in main_source
    assert "catalog_merchandising_router.routes" not in main_source


def test_legacy_showroom_contract_is_removed_from_merchandising():
    merchandising_source = (ROOT / "api" / "catalog_merchandising.py").read_text(
        encoding="utf-8"
    )

    assert "class ShowroomAppointmentIn" not in merchandising_source
    assert "class ShowroomAppointmentStatusIn" not in merchandising_source
    assert 'router.post("/showroom/appointments")' not in merchandising_source
    assert 'router.get("/showroom/appointments/me")' not in merchandising_source
    assert 'router.get("/admin/showroom/appointments")' not in merchandising_source
    assert 'router.patch("/admin/showroom/appointments/{appointment_id}")' not in merchandising_source
