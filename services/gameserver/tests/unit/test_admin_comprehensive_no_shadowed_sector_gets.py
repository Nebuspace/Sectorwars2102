"""Pin: admin_comprehensive must not re-register shadowed GET /sectors routes.

71dd2ae8 deleted GET /sectors, GET /sectors/{id}/planet, and GET
/sectors/{id}/port from admin_comprehensive.py because api.py mounts
admin_router first — FastAPI matches the first registered route, so those
handlers were permanently unreachable. They later returned (merge residual).
This test is the pin 71dd2ae8 lacked.

admin.py owns the live GET handlers. POST /sectors/{id}/planet and /port
and GET /universe/sectors/comprehensive stay on admin_comprehensive.
"""

from fastapi.routing import APIRoute

from src.api.routes import admin as admin_mod
from src.api.routes import admin_comprehensive as comp_mod

SHADOWED_GETS = (
    "/sectors",
    "/sectors/{sector_id}/planet",
    "/sectors/{sector_id}/port",
)


def _paths(router, method: str) -> set[str]:
    return {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and method in route.methods
    }


def test_admin_py_owns_live_sector_gets():
    gets = _paths(admin_mod.router, "GET")
    for path in SHADOWED_GETS:
        assert path in gets, path


def test_admin_comprehensive_has_no_shadowed_sector_gets():
    gets = _paths(comp_mod.router, "GET")
    for path in SHADOWED_GETS:
        assert path not in gets, path


def test_admin_comprehensive_keeps_live_sector_posts_and_comprehensive_list():
    posts = _paths(comp_mod.router, "POST")
    gets = _paths(comp_mod.router, "GET")
    assert "/sectors/{sector_id}/planet" in posts
    assert "/sectors/{sector_id}/port" in posts
    assert "/universe/sectors/comprehensive" in gets
