# Routes package
#
# `bang_galaxy` hosts the new sw2102-bang admin endpoints (Phase 1C of the
# bang-integration plan). It is wired into the main router by `api.py` so
# importing from this package surfaces the symbol for tests and tooling.
from src.api.routes.bang_galaxy import router as bang_galaxy_router  # noqa: F401
