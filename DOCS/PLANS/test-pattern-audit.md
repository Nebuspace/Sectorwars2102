# Test Pattern Audit — Gameserver & Stack

Audit prepared for the Translator Author subagent (Bang import service) so test code matches existing conventions on first write.

Scope: `services/gameserver/tests/`, `services/gameserver/pyproject.toml`, `services/gameserver/pytest.ini`, `services/gameserver/alembic.ini`, `e2e_tests/`, `services/admin-ui/`, `services/player-client/`.

---

## 1. Pytest Stack

**Runner**: `pytest ^7.4.0` (poetry dev group).
**Async**: `pytest-asyncio ^0.21.1` with `asyncio_mode = "auto"` — async tests do **not** need an explicit `@pytest.mark.asyncio` decorator (though many existing tests still apply it explicitly).
**Coverage tool installed**: `pytest-cov ^4.1.0` — installed but **no `--cov` invocation, no threshold, no `[tool.coverage]` section anywhere**. See section 6.
**Linters/formatters**: black, isort, ruff, mypy. ruff selects `E,F,W,C90,I,N,B,S` and ignores `S101,B008`.

Config file references:
- `services/gameserver/pyproject.toml:40-48` — dev dependencies.
- `services/gameserver/pyproject.toml:82-88` — `[tool.pytest.ini_options]`: `testpaths=["tests"]`, `addopts = "-v --tb=short"`, `asyncio_mode = "auto"`.
- `services/gameserver/pytest.ini:1-21` — duplicate root pytest config with extra markers (`unit, integration, api, model, service, slow, ship, trading, colonization`) and verbose log_cli formatting. **Note: both files exist; pytest.ini wins** for top-level keys. Pick `pytest.ini` markers for new tests so existing tooling recognises them.
- There is **also** a root-level `e2e_tests/conftest.py` (Python) at `e2e_tests/conftest.py:1-68` whose only job is to set env vars and sys.path for VS Code Test Explorer to discover gameserver tests from the host. It is **not** a Playwright fixture file.

Test layout:
```
services/gameserver/tests/
├── conftest.py                       # session-wide fixtures (db, client, admin_auth_headers)
├── utils.py                          # mock_settings_env autouse fixture (env loader)
├── mock_app.py / mock_config.py      # minimal FastAPI app for isolated tests (rarely used)
├── unit/                             # pure / mock-heavy (test_security, test_regional_governance, …)
├── integration/
│   ├── test_refresh_token.py         # async + DB tests
│   └── api/                          # TestClient-based route tests (test_*_endpoints.py, test_*_routes.py)
└── security/                         # middleware/header tests
```

---

## 2. DB Setup Pattern

**One real PostgreSQL database (Neon or docker-compose Postgres), no per-test schema, transactional rollback per test.**

- Env injection at import time, before any `src` import: `services/gameserver/tests/conftest.py:32` sets `ENVIRONMENT=testing`; `conftest.py:56-89` reads `DATABASE_URL` / `DATABASE_TEST_URL` from `.env` (workspace root) or docker-compose env, falls back to main DB when `DATABASE_TEST_URL` is unset (which is the case in this repo — `test_db_url = main_db_url` at line 67).
- Engine is created once at `conftest.py:138` with `create_engine(TEST_DATABASE_URL)` (plain Postgres, no StaticPool — the `StaticPool` import at line 97 is **unused**).
- `Base.metadata.create_all` is called inside the `db` fixture at `conftest.py:174` (idempotent — relies on existing migrations / tables).
- Per-test isolation pattern at `conftest.py:165-216`: open a **connection**, begin a **transaction**, bind a sessionmaker to that connection, hand it to the test, then `transaction.rollback()` + `connection.close()` in `finally`. This is the classic SQLAlchemy "outer transaction" pattern — everything the test does is rolled back.
- Dependency override: a module-level `_current_test_session` (`conftest.py:143`) is swapped in via `override_get_db` (`conftest.py:145-155`) so `TestClient` requests reuse the same transactional session as the test body. The override is installed once at module load (`conftest.py:158`).
- Admin user seeding inside the fixture at `conftest.py:187-205`: creates the admin user + `AdminCredentials` if not present so `admin_auth_headers` can log in. Idempotent.

**Critical naming inconsistency** — `conftest.py:166` defines the fixture as `db`, but `tests/integration/test_refresh_token.py:14,66,120` requests a fixture named `db_session`. There is **no** `db_session` fixture defined anywhere. Those refresh-token tests will fail to collect unless someone added a `db_session` alias upstream (none found in this audit). Translator Author: use `db` (the actual fixture name from conftest.py:166), not `db_session`.

No in-memory SQLite. No per-test schema. No Alembic invocation in the test path — schema is assumed to exist from `alembic upgrade head` run separately. `services/gameserver/alembic.ini:1-40` is a stock template; no test-specific section.

---

## 3. Mocking Conventions

The codebase is **heavily database-realistic** — tests prefer to hit Postgres rather than mock SQLAlchemy.

- **Unit tests mock the DB session** when service logic is pure and the DB call is one query. Example: `tests/unit/test_regional_governance.py:24-27` uses `AsyncMock()` for the session and `mock_db.execute.return_value.scalar_one_or_none.return_value = sample_region` (line 56-65). `MagicMock` is used for query result rows (lines 82-87).
- **Integration tests do not mock the DB** — they use the `db` fixture and write real rows. See `tests/integration/api/test_nexus_endpoints.py:22-59` (Region/Sector models created with `db.add(...)` + `db.flush()`).
- **External services are mocked at the env level** via `tests/utils.py:57-73`: fallback values for `GITHUB_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `STEAM_API_KEY`. Real OAuth providers are never called.
- **AI/LLM** is disabled by env: `e2e_tests/conftest.py:13` sets `AI_DIALOGUE_ENABLED=false`. Translator should follow this convention if it calls any AI subsystem.
- No `responses`, `httpx-mock`, or `pytest-httpx` is in the dependency tree — outbound HTTP that needs mocking is rare; tests prefer to construct request payloads and hit the FastAPI app directly via `TestClient`.

For the **bang translator**, which reads bang JSON and writes to Postgres: prefer integration-style tests using the `db` fixture against the real schema. Mock only the file/HTTP boundary (the bang JSON payload itself is the input — a fixture file, not a mock).

---

## 4. Fixture Conventions

- **Shared fixtures** live in `tests/conftest.py`. Only three are global: `app_fixture` (session), `db` (function), `client` (function), `admin_auth_headers` (function).
- **Test-local fixtures** are defined inline in the test module — see `tests/unit/test_docking_turns.py:15-37` (test_player) and `:40-59` (test_port), or `tests/integration/api/test_nexus_endpoints.py:22-34` (nexus_region) and `:36-59` (sample_nexus_sectors). The pattern: build SQLAlchemy ORM objects, `db.add`, `db.flush` (not `commit` — let the outer transaction roll back).
- Object IDs are generated with `uuid.uuid4()` inline (`test_nexus_endpoints.py:46`, `test_docking_turns.py:18`); no factory library (no factory_boy, no model_bakery).
- **There is no `tests/fixtures/` directory and no JSON fixture files** — every test builds objects in Python. The Translator Author will be **adding the first JSON fixture pattern**. Recommended location: `services/gameserver/tests/fixtures/bang/` with raw `.json` files, loaded via a small helper in `tests/fixtures/__init__.py` or a `bang_fixture` pytest fixture that does `json.loads(Path(__file__).parent.joinpath('bang', name).read_text())`.

---

## 5. Async Test Patterns

`pytest-asyncio` with `asyncio_mode = "auto"` (`pyproject.toml:88`), so async test functions run without an explicit marker — but the existing tests apply `@pytest.mark.asyncio` anyway, e.g. `tests/integration/test_refresh_token.py:13, 65, 119`. Follow that style for clarity.

Async examples:
- Async route test with `AsyncClient`: `tests/integration/test_refresh_token.py:14-62` — note this file uses `httpx.AsyncClient` (imported at line 4) and requests `db_session` (broken — see §2). New async tests should pin `client: TestClient` (sync) unless they explicitly need `await client.post(...)` for concurrent request testing.
- Async race-condition pattern with `asyncio.gather`: `tests/integration/test_refresh_token.py:90-100` — useful template for the bang translator's async job queue concurrency tests.
- Async unit pattern with `AsyncMock`: `tests/unit/test_regional_governance.py:24-27, 55-65`.

For the translator's async job tracking, copy the `asyncio.gather` race-condition template at `test_refresh_token.py:90-100`.

---

## 6. Coverage Tooling

- `pytest-cov ^4.1.0` is installed (`pyproject.toml:43`).
- **There is no `--cov` flag anywhere in `addopts`, no `.coveragerc`, no `[tool.coverage]` block, no CI step found, no threshold enforcement.**
- CLAUDE.md (`Sectorwars2102/CLAUDE.md`, Phase 4) aspires to >90% coverage but the tooling is not wired up.

**To run locally with coverage**:
```bash
docker compose exec gameserver poetry run pytest --cov=src --cov-report=term-missing tests/
```

**Recommendation for the translator**: pin a coverage target in your test plan even though the repo lacks one. Aim for ≥90% on `bang_import_service.py` since it's a translation layer where edge cases matter and there's no existing baseline to compete with.

---

## 7. E2E Patterns

Stack-wide e2e lives in `/e2e_tests/` (not under `services/`), runs via Playwright TypeScript.

- Config: `e2e_tests/playwright.config.ts:22-89`. Two projects — `admin-tests` (port 3001) and `player-tests` (port 3000), each matched by directory glob. Global setup/teardown at `e2e_tests/global-setup.ts` and `global-teardown.ts`.
- Per-service Playwright configs also exist: `services/admin-ui/` references one (`package.json:40-42` — `test:e2e`, `test:e2e:ui`, `test:e2e:docker` via custom node scripts in `scripts/`) and `services/player-client/playwright.config.ts:1-45` (sole project, chromium-only, `reuseExistingServer: true`).
- No Vitest, no Jest, no React Testing Library — **the frontends have no unit tests, only e2e**.

**Auth in e2e** (this matters for Phase 4 of the bang integration):
- `e2e_tests/fixtures/auth.fixtures.ts:16-52` extends `@playwright/test` with `adminCredentials` (hardcoded `admin/admin`) and `playerCredentials` (from `TEST_ACCOUNTS` created in global setup).
- `e2e_tests/utils/auth.utils.ts:10-213` (`loginAsAdmin`) walks the **actual login form**, with several fallback paths: form fill → click → if-still-on-/login fall back to `axios.post('/api/v1/auth/login')` to grab tokens → write tokens into `localStorage` → reload.
- **Mock-auth fallback**: `useMockAuthentication` at `auth.utils.ts:218-257` sets `localStorage.accessToken = 'mock-access-token-for-testing'` and proceeds. This is used **only when the real login form fails to render**; it does **not** make the backend authenticate, so any API calls after will 401. Useful for pure-UI render checks, not for end-to-end DB-touching flows.

**Implication for Phase 4 (dev JWT path)**: there is no first-class "dev JWT helper" in this repo. The existing pattern is "POST `/api/v1/auth/login` with admin/admin and stash the token". For new bang e2e tests, the cleanest approach is to follow that same pattern (real login, real token) rather than inventing a dev-only bypass. If a bypass is required, add a typed Playwright fixture under `e2e_tests/fixtures/` (e.g. `bangAdminToken`) that performs the login once per worker and yields the bearer header.

---

## 8. Recommendations for Translator Author

1. **Test layout**: put unit tests at `services/gameserver/tests/unit/test_bang_import_service.py` and DB-touching integration tests at `services/gameserver/tests/integration/test_bang_import_service.py`. If you add API routes for triggering imports, route tests go at `services/gameserver/tests/integration/api/test_bang_import_routes.py`. Match the existing naming (`test_*.py`, `Test*` classes).

2. **Fixture files**: introduce the first JSON fixture directory at `services/gameserver/tests/fixtures/bang/` containing small, deterministic bang exports (e.g. `minimal_galaxy.json`, `sector_with_port.json`, `malformed_missing_field.json`). Wire a helper fixture in a new `tests/fixtures/__init__.py` or local conftest:
   ```python
   @pytest.fixture
   def bang_payload(request):
       name = request.param if hasattr(request, 'param') else 'minimal_galaxy.json'
       path = Path(__file__).parent / 'fixtures' / 'bang' / name
       return json.loads(path.read_text())
   ```
   Use `@pytest.mark.parametrize('bang_payload', ['minimal_galaxy.json'], indirect=True)` to swap payloads per test.

3. **DB pattern**: depend on the existing `db` fixture from `tests/conftest.py:166` (NOT `db_session` — that name is referenced in `test_refresh_token.py` but not defined). The transactional rollback handles cleanup; do not call `db.commit()` in tests — use `db.flush()` so the outer transaction can still roll back. Build ORM objects inline as `test_nexus_endpoints.py:36-59` does.

4. **Async + concurrency**: for async job queue tests, copy the `asyncio.gather` pattern at `tests/integration/test_refresh_token.py:90-100`. Keep `@pytest.mark.asyncio` decorators explicit (matches house style even though `asyncio_mode=auto`).

5. **Coverage target**: aim for ≥90% line coverage on `bang_import_service.py` and run with `poetry run pytest --cov=src/services/bang_import_service --cov-report=term-missing tests/unit/test_bang_import_service.py tests/integration/test_bang_import_service.py`. There is no repo-wide threshold to enforce, so the translator should set its own bar and document it in the PR.

6. **E2E (Phase 4)**: place admin-UI bang import flow tests at `e2e_tests/admin/ui/admin-ui-bang-import.spec.ts` so they're picked up by the `admin-tests` Playwright project (`playwright.config.ts:71-79`). Authenticate via real login (`loginAsAdmin` from `e2e_tests/utils/auth.utils.ts:10`), not mock-auth, since the import flow must reach the backend. If you need a token directly (not a UI flow), add a fixture in `e2e_tests/fixtures/` that does `axios.post('/api/v1/auth/login', {username:'admin', password:'admin'})` once per worker.

7. **Markers**: tag tests with the existing `unit` / `integration` / `api` markers declared in `pytest.ini:11-20`. Consider proposing a new `bang` marker in `pytest.ini` so the suite can be run in isolation: `pytest -m bang`.

---

## Gaps to flag upstream (not blocking, but document)

- `tests/integration/test_refresh_token.py` requests a `db_session` fixture that does not exist. Either the file is dead, or there's an out-of-tree conftest. Worth confirming with the team before mirroring the pattern.
- No coverage threshold despite `pytest-cov` being installed and CLAUDE.md targeting >90%.
- Two pytest config files (`pyproject.toml` and `pytest.ini`) with overlapping but non-identical content. Markers only live in `pytest.ini`; `asyncio_mode=auto` only in `pyproject.toml`. Both are loaded; merge would be cleaner.
- The `StaticPool` import in `tests/conftest.py:97` is unused (leftover from an earlier in-memory SQLite plan).
- `tests/utils.py` is a parallel env-loader autouse fixture that overlaps with the conftest env setup; behaviour when both run is "last write wins" on env vars.
