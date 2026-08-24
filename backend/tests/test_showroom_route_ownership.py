from pathlib import Path

from fastapi.routing import APIRoute

from backend.api import catalog_merchandising, catalog_showroom
from backend.main import app


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
        if isinstance(route, APIRoute)
        for method in route.methods
        if method in {"GET", "POST", "PATCH", "PUT", "DELETE"}
    ]


def test_showroom_routes_have_one_physical_owner():
    merchandising_operations = set(_operations(catalog_merchandising.router.routes))
    canonical_operations = set(_operations(catalog_showroom.router.routes))

    assert merchandising_operations.isdisjoint(EXPECTED_SHOWROOM_OPERATIONS)
    assert canonical_operations == EXPECTED_SHOWROOM_OPERATIONS


def test_application_registers_each_showroom_operation_once():
    application_operations = _operations(app.routes)
    expected_application_operations = {
        (f"/api{path}", method) for path, method in EXPECTED_SHOWROOM_OPERATIONS
    }

    for operation in expected_application_operations:
        assert application_operations.count(operation) == 1

    openapi = app.openapi()["paths"]
    for path, method in expected_application_operations:
        assert method.lower() in openapi[path]


def test_legacy_showroom_contract_and_startup_route_surgery_are_removed():
    merchandising_source = (ROOT / "api" / "catalog_merchandising.py").read_text(
        encoding="utf-8"
    )
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "class ShowroomAppointmentIn" not in merchandising_source
    assert "class ShowroomAppointmentStatusIn" not in merchandising_source
    assert 'router.post("/showroom/appointments")' not in merchandising_source
    assert 'router.get("/showroom/appointments/me")' not in merchandising_source
    assert 'router.get("/admin/showroom/appointments")' not in merchandising_source
    assert 'router.patch("/admin/showroom/appointments/{appointment_id}")' not in merchandising_source
    assert "_REPLACED_CATALOG_SHOWROOM_ROUTES" not in main_source
    assert "catalog_merchandising_router.routes" not in main_source
