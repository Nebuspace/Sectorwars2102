# monk — memory · project: Sectorwars2102

## What I've learned about this project

- Build: `npm run build` (Vite) · Types: `npx tsc --noEmit` · Tests: `npx vitest run <glob>`
- Test harness is jsdom + `react-dom/client` createRoot + act() — NO @testing-library/react. Mirrors StatusBar.smoke.test.tsx pattern.
- `cockpit-shell.css` is the RATIFIED artifact baseline (do not modify) — defines `.mon`/`.mhead`/`.mbody`/`.skrow`/`.skey` etc.
- `DeckPageTabs` is already imported in `GameDashboard.tsx` (line 26). No import change needed to use it.
- Deck monitors use: `.mon <name>-monitor > .mhead(.mtitle + buttons) + .mbody(role=tabpanel) + .skrow(DeckPageTabs)`.
- `DeckPageTabs` renders null for < 2 pages. SpaceDock (1 venue) → no DeckPageTabs, no skrow.
- The `station-monitor` migration required replicating trading-interface.css compaction rules under `.station-monitor .station-venue-body` in cockpit.css (trading-interface.css is out of scope to touch).
- `.mbody` brings `font-size:.62em; color:#8CA2BA; padding:.6em .8em` — override these for venue contexts that have their own comprehensive CSS (`font-size:1em; color:inherit; padding:0` on `.station-monitor .mbody`).
- `git diff --name-only` to confirm scope: only owned player-client paths.

## How I operate here (my working notes)

- For CSS class migrations: grep all consumers first, then check CSS files for dead vs live selectors.
- Always check test files in `__tests__/` that might assert on old class names before removing them.
- The `.screen-hud-content` class adds margins + side borders (old CRT aesthetic) — do NOT carry it forward into `.mon` anatomy contexts.

## Admin route patterns (gameserver)

- Auth: `admin: User = Depends(require_admin)` + `db: Session = Depends(get_db)` — imported from `src.auth.dependencies` and `src.core.database`.
- Router prefix in the file: `router = APIRouter(prefix="/admin", tags=["..."])`. Registered in api.py WITHOUT an extra `prefix="/admin"` (the router carries it).
- Ship model: fields are `owner_id`, `type` (Enum, use `.value`), `sector_id` — NOT `player_id`, `ship_type`, `current_sector_id`.
- MarketTransaction table: `enhanced_market_transactions`. Has `timestamp`, `total_value`, `profit_margin`, `commodity`, `transaction_type` (Enum).
- pg_stat_activity queries work with standard SELECT — no special extension needed.
- pytest IS runnable Mac-local after `poetry install --no-root` (venv drifts from lockfile; earlier "not available" note was stale). No live Postgres needed for DB-free unit tests — see below.
- Sibling admin routers re-export their service's public interface (errors + functions) even mid-refactor-split — e.g. `contract_service.py` was mid-split into `contract_dispute.py`/`contract_escrow_core.py`/`contract_insurance.py` uncommitted in the tree while I built against it, but `from src.services.contract_service import resolve_dispute, ContractError, ...` still worked because the split re-exports via `import X as X`. Trust the documented public interface from the WO; don't go spelunking for the "real" module.

## Running gameserver tests DB-free on the Mac (no live Postgres)

- `tests/conftest.py` is loaded for EVERY test file (even `tests/unit/`) and unconditionally requires `DATABASE_URL` + `ARIA_ENCRYPTION_KEY` (valid Fernet key) + `JWT_SECRET` at import time, even if the test never touches the `db`/`client`/`admin_auth_headers` fixtures.
- Reuse the repo's own DB-free CI lane env (`.github/workflows/ci-build-test.yml`): `GAMESERVER_CI_DB_FREE=1 ENVIRONMENT=testing DATABASE_URL="postgresql://dummy:dummy@localhost:5432/dummy" JWT_SECRET=<32+ chars> ADMIN_USERNAME=... ADMIN_PASSWORD=... ARIA_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")`.
- `conftest.py`'s `pytest_collection_modifyitems` hook (under `GAMESERVER_CI_DB_FREE=1`) auto-skips any test whose `item.fixturenames` contains the literal strings `db`/`client`/`admin_auth_headers` — this is a NAME match, not a dependency-graph match. If you write your own DB-free `TestClient(app)` fixture, do NOT name it `client` or it gets false-positive-skipped; pick another name (e.g. `dispute_client`).
- **Host header gotcha**: `ENVIRONMENT=testing` flips `main.py`'s `TrustedHostMiddleware` off its dev wildcard (`["*"]`) onto the restrictive list (`["localhost", "*.app.github.dev", "*.repl.co"]`). `TestClient(app)`'s default `base_url="http://testserver"` sends a Host header that matches NONE of those → every request gets a blanket `400 "Invalid host header"` from the middleware, before any route/auth code runs — looks like a route bug but isn't. Fix: `TestClient(app, base_url="http://localhost")`.
- Authz-before-mutation proof pattern: override `get_current_user` (not `require_admin`/`get_current_admin_user`) so the real `is_admin` check still executes — that's what makes a 403 test a genuine proof instead of a rubber stamp. Order route params `admin: Depends(require_admin)` BEFORE `db: Depends(get_db)` so an auth failure never resolves `get_db` at all (FastAPI resolves top-level `Depends()` in signature order; an earlier one raising stops the rest) — real Postgres never touched even without overriding `get_db`.

## RBAC scope expansion (19→26, 2026-07-17)

- 7 operational scopes in `auth/admin_scopes.py`; HIGH_IMPACT += galaxy.manage, players.adjust_credits, ships.manage (+ disputes.resolve already there) → 11 total.
- Mutating admin routes remap off PLAYERS_VIEW per design-brief rubric; GETs stay on PLAYERS_VIEW.
- Seed migration pattern: idempotent INSERT skip-if-active-grant-exists (same as e2a7f3c8b5d1); new revision parents e2a7f3c8b5d1.
- PATCH player with credits+status mixed → PLAYERS_ADJUST_CREDITS (more-privileged money path per Max brief).
- Ambiguous ops without a clean scope fit: player create-from-user/bulk → PLAYERS_ADJUST_CREDITS; game-events/analytics-snapshot/ai-model-action → GALAXY_MANAGE; drone admin → SHIPS_MANAGE; faction rep → existing PLAYERS_ADJUST_REP.

- `src/auth/admin_scopes.py` — 26 canonical scopes; `ALL_SCOPES`, `HIGH_IMPACT_SCOPES`, `META_SCOPES`, `SCOPE_DESCRIPTIONS` frozensets/dict; module-level assert `set(SCOPE_DESCRIPTIONS)==ALL_SCOPES`.
- `src/models/admin_scope_grant.py` — AdminScopeGrant; `is_active` = property (revoked_at IS None); FK ondelete=CASCADE on user_id; SET NULL on granted_by/revoked_by.
- `src/models/admin_action_log.py` — AdminActionLog; admin_user_id FK ondelete=SET NULL (preserves audit trail on user delete).
- Migration `e2a7f3c8b5d1` revises `d4f8b16a92c1`; seed is idempotent per-row (no UNIQUE constraint on pair — revoked+active rows coexist; check existing active grant before inserting).
- SQLAlchemy ORM instances for DB-free tests: use normal constructor `Model(field=val)`, not `__new__` (bypasses mapper init → AttributeError on instrumented attrs).

- `PlanetUpdateRequest` placed after `create_planet_in_sector` (~line 1995), before port create.
- Auth: `get_current_admin` (same as all routes in this file). `get_current_user` is what tests must override for genuine 403 proof (the real is_admin check runs).
- Colonized guard: fail-closed — ANY of owner_id set, colonized_at set, population > 0, or status in {COLONIZED, DEVELOPED, DYING, RESTRICTED, TERRAFORMING} → 409. db.delete never called.
- `planet_data.model_dump(exclude_unset=True)` — use model_dump (Pydantic v2), not .dict().
- PlanetStatus is imported inline inside the route (not at module level; top-level only has Planet).
- Admin-ui: `api` from `../../utils/auth` is the axios instance used for all API calls in admin-ui components.

## Multi-account admin route (PADMIN-multiacct-review)

- Models are in `src/models/multi_account.py` (MultiAccountCluster + MultiAccountFlag). Detection service NOT built yet — queue starts empty.
- Route prefix `/admin/multi-account` (carried by the router). Registered in api.py exactly like `admin_contract_disputes_router`.
- `_ALLOWED_DECISIONS` excludes PENDING — routing code validates this before touching DB.
- Test fixture named `mac_client` (not `client`) to avoid conftest name-match false-skip. `base_url="http://localhost"` for TrustedHostMiddleware.
- Mock cluster needs `.flags = []` (list, not MagicMock()) so `len(c.flags)` in `_serialize_cluster` works correctly.
- After Phase B sweep to ``require_scope``: route tests that override ``get_db`` must let the **first** ``query().filter().first()`` return a truthy grant row (scope check) before cluster/entity lookups — see ``_db_passing_scope`` helper in ``test_admin_multi_account.py``.

## tw2002-aiclient (separate repo)

- Stack: Python 3.14 + `.venv/` (use `.venv/bin/python -m pytest`, NOT `python -m pytest`)
- Test runner: `pytest>=8.0` (from `requirements-dev.txt`), installed in `.venv/`
- `path_to_sector(graph, start, goal)` returns endpoint-inclusive tuple; `turns = len(path) - 1`.
- Pricing model: `buy_cost = floor*(1+(mult-1)*src_pct/100)`; `sell_offer = floor*(1+(mult-1)*(100-snk_pct)/100)`. Margin > 0 iff `src_pct + snk_pct < 100`. Default pct=50 for both → margin=0, filtered — always use pct=0 or asymmetric pcts in tests.
- `DEFAULT_CEILING_MULTIPLIER=2.0` → at pct=100, price = 2×floor; at pct=0, price = floor.
- `build_trade_hops(world_id)` returns `(list[TradeHop], str)` — callers discard the note.
- `trade_driver.py` already existed and imported `DEFAULT_CEILING_MULTIPLIER`, `DEFAULT_FLOOR_PRICES`, `_commodity_price` from `trade_adapter` — must honour that API contract.

## Gotchas — things that bit me (don't repeat)

- Adding `screen-hud-content` as an extra class on a `mbody` element seems safe but actually cascades the old CRT borders (border-left/border-bottom) and margin into the new anatomy — avoid it.
- `trading-interface.css` scopes compaction rules under `.screen-hud-content` parent — when removing that class from the venue wrapper, replicate those rules under the new selector in cockpit.css.
