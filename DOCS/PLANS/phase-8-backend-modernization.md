# Phase 8 — Backend modernization

**Status**: DRAFT — awaiting Max's go-ahead
**Owner**: Claude (implementation), Samantha (review)
**Scope branch target**: `master`
**Predecessor**: Phase 7 (code-scanning + dep bumps via PR #38) complete
**Prerequisite check**: stage stack on interstitch should be running cleanly post-Phase-7 before starting

---

## Goal

Land the two open Dependabot gameserver PRs (#41 majors, #42 safe-bumps) and the deferred python-jose → pyjwt migration. Each carries real breaking changes that need a verification pass against the running gameserver, not a bulk merge.

## What's in scope

### PR #42 — `safe-bumps` (despite the name, contains substantial cascades)

| Dep | From → To | Concern |
|---|---|---|
| `anthropic` | 0.18.1 → 0.105.2 | **87 minor versions.** SDK had multiple API rewrites — `client.messages.create()` vs `client.beta.messages.create()`, message-format changes. Affects `services/gameserver/src/services/enhanced_ai_service.py` and the AI dialogue path. |
| `pydantic` | 2.4 → 2.13 | 9 minor versions. Field validation edge cases changed; `model_dump()` defaults shifted; `Optional[X]` vs `X \| None` parsing. Touches every Pydantic schema. |
| `ruff` | 0.0.287 → 0.15.15 | Pre-1.0 → post-1.0; default rule set changed; rule rename cascade. Affects CI lint job. |
| `scipy` | 1.11 → 1.17 | Minor bumps but scipy has had behavioral changes per minor. Used by? — `grep` first. |
| `prophet` | 1.1.4 → 1.3.0 | Time-series forecasting; if used in market trend code, output format changed. |
| `fastapi` | 0.115 → 0.125 | Incremental from our Phase 7 bump. `@app.on_event` decorator now emits warnings — Phase 8 should also migrate to lifespan handler. |
| `sqlalchemy` | 2.0.20 → 2.0.50 | Patch chain in 2.0.x — should be safe. |
| `alembic` | 1.12 → 1.18 | Minor bumps; new operation hooks but backwards compatible. |
| `uvicorn` | 0.23 → 0.48 | Multi-minor; `--workers` semantics + signal handling stable. |
| `httpx` | 0.24 → 0.28 | We're already on 0.27 from Phase 7 bumps; this brings 0.27 → 0.28 (small). |
| Others | various | psycopg2-binary, asyncpg, pydantic-settings, python-jose, python-multipart, email-validator — minor bumps, low risk |

### PR #41 — `majors` (every entry has real breaking changes)

| Dep | From → To | Concern |
|---|---|---|
| `redis` | 5.0.1 → 8.0.0 | **Major behavioral changes**: default `decode_responses` semantics, connection pool defaults, pubsub message format. Affects gameserver redis access (REDIS_URL) and the realtime bus. |
| `openai` | 1.12 → 2.38 | **Complete SDK rewrite from v1 to v2**. `openai.OpenAI()` constructor, response-object shape, streaming API. If AI dialogue uses OpenAI, must rewrite call sites. |
| `numpy` | 1.24 → 2.4 | **Breaking changes**: removed deprecated aliases (`np.float`, `np.int`), `copy=False` default behavior changed, dtype promotion rules. Affects scientific code (galaxy math, market analysis). |
| `websockets` | 11 → 16 | Multi-major; new asyncio integration, removed sync API. Affects WebSocket handlers. |
| `bcrypt` | 4 → 5 | Hash format compatibility preserved; verify the auth path still validates old hashes. |
| `mypy` | 1.20 → 2.1 | Strict-mode defaults changed; type-checking surface broader. Likely surfaces new errors in `gameserver/src/`. |
| `pytest-asyncio` | 0.21 → 1.4 | Mode default changed from `legacy` to `strict`; existing tests may need `@pytest_asyncio.fixture`. |
| `pytest-cov` | 4 → 7 | Coverage plugin API changes; reporting format tweaks. |
| `pandas` | 2.0 → 2.3 | Minor format changes; mostly fine. |
| `isort` | 5 → 8 | New defaults; will reflow imports across the codebase on next save. |
| `flake8` | 6 → 7 | New default rule set. |
| `qrcode` | 7 → 8 | Image rendering output format changed; verify MFA setup QR still works. |
| `argon2-cffi` | 23 → 25 | Salt length defaults changed; hash format preserved. |

### Side quest — python-jose → pyjwt migration

The `ecdsa` Minerva CVE (CVE-2024-23342, high severity, no upstream fix) is pulled transitively by `python-jose`. Migrating to `pyjwt[crypto]` clears it. ~50-100 LOC of refactor in `services/gameserver/src/auth/jwt.py` and its callers.

### Side quest — FastAPI lifespan migration

`@app.on_event("startup")` and `@app.on_event("shutdown")` in `services/gameserver/src/main.py` are deprecated in FastAPI 0.115. Replace with the lifespan context manager pattern. ~30 LOC.

---

## Verification strategy

Multi-stage validation against the stage tunnel:

### Stage 1 — Local typecheck (Mac)
- `cd services/gameserver && poetry install` after each PR's lock applies
- `poetry run mypy src/ --ignore-missing-imports` — baseline before / after each PR; net new errors must be reviewed
- `poetry run pytest tests/unit/test_bang_translator.py tests/unit/test_bang_invoke_mock.py` — bang translator unit tests still pass

### Stage 2 — Build on interstitch
- `git pull && docker compose --profile development up -d --build gameserver` — clean rebuild succeeds
- Container starts without import errors; no deprecation warnings about `@app.on_event` (after lifespan migration)
- Health endpoint returns 200

### Stage 3 — Behavioral checks (admin UI on stage)
For each PR landing:
- **Redis bump (#41)**: log into admin UI; verify WebSocket realtime bus still delivers events; check `redis-cli MONITOR` against interstitch's redis-cache
- **OpenAI bump (#41)**: trigger an AI dialogue flow (first-login or NPC interaction); verify response renders
- **Anthropic bump (#42)**: same as above but with Anthropic provider
- **NumPy/pandas (#41)**: trigger galaxy generation via `POST /admin/galaxy/jobs`; verify stats panel populates
- **bcrypt (#41)**: log in with the bootstrap admin password; verify it still authenticates
- **websockets (#41)**: hit `/api/v1/admin/galaxy/jobs/{id}/stream` SSE endpoint; verify it streams
- **qrcode (#41)**: enable MFA on admin user; verify QR code renders + a TOTP app can scan it
- **FastAPI lifespan**: container restart cycle; verify startup hook (bang orphan recovery) still runs

### Stage 4 — Translator integration test
- Generate a galaxy end-to-end via the bang sidecar pulling `docker.io/drxelanull/sw2102-bang:1.3.0`
- Verify the resulting Galaxy row has all 3 regions populated correctly
- This exercises the SQLAlchemy + Alembic + pydantic stack together

---

## Suggested PR landing order

| Order | PR | Verification | Notes |
|---|---|---|---|
| 1 | **FastAPI lifespan migration** (small standalone PR) | Container restart cycle | Unblocks #42's incremental fastapi bump cleanly |
| 2 | **#42 safe-bumps** | Stages 1–3 (with anthropic + ruff focus) | Anthropic SDK rewrite is the biggest risk here; smoke `/admin` first |
| 3 | **#41 majors** | Stages 1–4 (with redis + openai + numpy focus) | After #42 lands; rebase #41 if needed |
| 4 | **python-jose → pyjwt** (small standalone PR) | Login flow + MFA flow on stage | Clears the ecdsa Minerva CVE |

## Risk + rollback

- **Risk**: any one of #42 / #41's bumps could break gameserver at runtime in a way mypy doesn't catch. Stage validation is the safety net.
- **Rollback per PR**: each is a single squashed commit on master — `git revert <sha>` then push. Interstitch picks up the revert on next `git pull && docker compose up --build`.
- **Rollback for python-jose migration**: branched separately; easy revert.

## Open questions for Max before starting

1. Is the AI dialogue path actively used today (worth verifying the anthropic/openai bumps), or is it stubbed/disabled on stage? If the latter, validation drops to a typecheck pass.
2. MFA: enabled for any current user? If no, qrcode bump is no-op for validation.
3. Time budget: this is realistically 4-6 hours of careful work across the four steps. Want it as one session or split across sessions?

## What this phase does NOT include

- Code refactors beyond what the bumps force
- Performance optimization
- New features

---

*Phase 8 lands the backend-side modernization queue. Phase 9 ([frontend modernization](./phase-9-frontend-modernization.md)) is independent and can run in parallel.*
