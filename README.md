# SectorWars 2102

> A web-based multi-regional space-trading simulation — own territories, govern economies, and trade across a living universe.

![License](https://img.shields.io/badge/License-PolyForm_NC_1.0.0-blue)

---

> 📖 **Full design documentation:** https://sw2102-docs.shouden.us
> Source: https://github.com/Nebuspace/sw2102-docs

---

## Overview

SectorWars 2102 is a turn-based space-trading game built around a **Multi-Regional Universe**. Players navigate sectors, trade commodities, and can own and govern entire regional territories — setting economic policy, managing planetary infrastructure, and engaging in inter-regional diplomacy. A Central Nexus hub (5,000 sectors) connects all regions. The game features AI-powered trading assistance (ARIA), dynamic NPC interactions, and persistent progression across colonization, combat, and economic systems.

---

## Documentation

The canonical game specification, architecture docs, ADRs, and feature plans live in the public docs site:

- **Live docs:** https://sw2102-docs.shouden.us — auto-deploys to Cloudflare on every push to `main`
- **Architecture & services:** https://sw2102-docs.shouden.us (see the ARCHITECTURE section)
- **Features & economy:** https://sw2102-docs.shouden.us (see the FEATURES section)
- **ADRs & decisions:** https://sw2102-docs.shouden.us (see the ADR section)
- **Source:** https://github.com/Nebuspace/sw2102-docs

Additional in-repo references:
- **Dev guide / project context:** [`CLAUDE.md`](./CLAUDE.md)
- **Remote AI implementer seats:** joining from another machine? You are a remote seat — see the *Remote seats* section of [`CLAUDE.md`](./CLAUDE.md) for your kickstart instruction. Request the private onboarding pack from the hub (orchestrator); connection details are never stored in this repo.
- **AI system specs:** [`AISPEC/`](./AISPEC/)
- **E2E test suite:** [`e2e_tests/`](./e2e_tests/)

---

## Architecture

Microservices orchestrated with Docker Compose and profile-gating. Every service is behind a profile — a bare `docker compose up` starts nothing; always pass `--profile`.

### Services

| Service | Profile(s) | Role |
|---|---|---|
| `database` | `development`, `default` | PostgreSQL — single-server dev database |
| `central-nexus-db` | `multi-regional`, `production` | PostgreSQL — Central Nexus |
| `redis-cache` | `development`, `default`, `multi-regional` | Cache / session store |
| `redis-nexus` | `multi-regional`, `production` | Nexus-scoped Redis |
| `gameserver` | `development`, `default` | FastAPI game API (single-server dev) |
| `central-nexus-server` | `multi-regional`, `production` | FastAPI — Central Nexus service |
| `player-client` | `development`, `default`, `multi-regional` | React cockpit (Vite dev server) |
| `admin-ui` | `development`, `default`, `multi-regional` | React admin interface (Vite dev server) |
| `nginx-gateway` | `development`, `default`, `multi-regional` | Reverse proxy / gateway |
| `region-manager` | `development`, `default`, `multi-regional` | Regional orchestration service |
| `prometheus` / `grafana` | `monitoring`, `production` | Observability stack |
| `regional-server-template` | `regional-template` | Regional shard template |

### Stack

| Layer | Technology |
|---|---|
| **Backend** | FastAPI ≥0.138.1 (Python 3.11), Starlette ≥1.3.1, SQLAlchemy 2, Alembic, Poetry |
| **Database** | PostgreSQL 15 |
| **Cache** | Redis |
| **Auth** | JWT-based |
| **Frontend** | React 19 + TypeScript, Vite 8 |
| **3D Visualization** | three.js, react-three-fiber (player cockpit) |
| **Charts / Analytics** | chart.js, D3.js |
| **Testing** | Pytest (backend), Playwright (E2E), React Testing Library |
| **Containers** | Docker + Docker Compose (profile-based) |

### Ports (dev host)

| Service | Port |
|---|---|
| Player Client | `3000` |
| Admin UI | `3001` |
| Game API | `8080` |
| API Docs (Swagger) | `8080/docs` (when `DEBUG=true`) |

---

## Quick Start

### Prerequisites

- **Mac / local machine:** Node.js 20+ (22 LTS recommended), Python 3.11+, git
- **Remote dev host:** Docker + Docker Compose, accessible via Tailscale

> **Do not run `docker build`, `docker run`, or `docker compose` on your Mac.** The full stack runs on a remote Linux dev host. Running Docker locally alongside Claude Code throttles CPU to ~20%.

Infrastructure details, SSH access, and runbooks live in `~/github/ServerSetup/` (local-only). See `ServerSetup/docs/services/sectorwars-hosting.md` for host names and access.

### 1. Clone the repo

```bash
git clone https://github.com/Nebuspace/Sectorwars2102.git
cd Sectorwars2102
```

### 2. Start the stack (remote dev host, via SSH)

```bash
# SSH into the dev host
ssh <dev-host>

# In /data/sectorwars-dev (or the configured project root):
docker compose --profile development up -d
```

Brings up: `database`, `redis-cache`, `gameserver`, `player-client`, `admin-ui`, `nginx-gateway`, `region-manager`.

### 3. Apply database migrations (remote dev host)

```bash
docker compose exec gameserver poetry run alembic upgrade head
```

### 4. Access services

With Tailscale connected, reach the dev host's exposed ports:

- **Player Client:** `http://<dev-host>:3000`
- **Admin UI:** `http://<dev-host>:3001`
- **Game API:** `http://<dev-host>:8080`
- **API Docs:** `http://<dev-host>:8080/docs`

### Compose profiles

| Profile | Use case |
|---|---|
| `development` / `default` | Standard dev — single gameserver + supporting services |
| `multi-regional` | Multi-regional dev with Central Nexus |
| `production` | Production topology |
| `monitoring` | Add Prometheus + Grafana |
| `regional-template` | Spin up a regional shard template |

---

## Development

### Mac-local commands (no Docker required)

```bash
# Player Client — type-check then build
cd services/player-client
npm install
npx tsc --noEmit      # always run before pushing (Vite does NOT type-check on build)
npm run build

# Admin UI
cd services/admin-ui
npm install
npx tsc --noEmit
npm run build

# Gameserver — linting + unit tests (requires local venv or Poetry)
cd services/gameserver
poetry install
poetry run ruff check .
poetry run pytest     # unit tests; integration tests require the running stack
```

> **Note:** `npm run build` (Vite/esbuild) does not type-check. Run `npx tsc --noEmit` explicitly before deploying — type errors and undefined-name bugs ship silently otherwise. The gameserver equivalent: `ruff check --select F821` catches orphaned-variable / rename crashes that `py_compile` misses.

### Remote dev host commands (via SSH or `docker compose exec`)

```bash
docker compose exec player-client npm run build
docker compose exec player-client npm run lint
docker compose exec admin-ui      npm run lint
docker compose exec gameserver    poetry run pytest
docker compose exec gameserver    poetry run ruff check .
```

### Migrations

```bash
# Apply
docker compose exec gameserver poetry run alembic upgrade head
# New revision
docker compose exec gameserver poetry run alembic revision -m "description"
# Status
docker compose exec gameserver poetry run alembic current
```

Migrations are **additive only** — nullable columns or new tables. No destructive schema changes without explicit sign-off.

---

## Testing

### Backend (Gameserver)

```bash
# Remote dev host — full suite (requires running stack)
docker compose exec gameserver poetry run pytest

# Mac-local — unit tests only (if local Poetry env is set up)
cd services/gameserver && poetry run pytest
```

### End-to-End (Playwright)

E2E tests target the remote dev host via Tailscale:

```bash
# From Mac, with Tailscale connected and dev host running:
npx playwright test -c e2e_tests/playwright.config.ts

# Specific suites
npx playwright test -c e2e_tests/playwright.config.ts --project=admin-tests
npx playwright test -c e2e_tests/playwright.config.ts --project=player-tests
```

---

## Git Workflow

| Branch | Role |
|---|---|
| **`master`** | Validated, production-ready code. Protected by a repo ruleset: PRs required, no fast-forward, CodeQL + code-quality gates, merge-commit method. |
| **`feat/expeditions-vista`** | Active development branch. |

Promote dev → `master` via PR when a tranche is ready. Feature branches merge into `feat/expeditions-vista` first; a passing PR gate is required to merge to `master`.

---

## Contributing

1. Branch off `feat/expeditions-vista` (or the current active dev branch).
2. Keep changes scoped — one logical concern per PR.
3. Mac-local gates before opening a PR: `npx tsc --noEmit` (frontend) + `poetry run ruff check .` + `poetry run pytest` (backend).
4. PRs to `master` require CodeQL + code-quality gate to pass.
5. Migrations must be additive (nullable columns / new tables); destructive changes require explicit sign-off.
6. See [`CLAUDE.md`](./CLAUDE.md) for the full dev guide and coordination protocol.

---

## License

SectorWars 2102 is licensed under the **PolyForm Noncommercial License 1.0.0** — free to use and self-host for any **noncommercial** purpose; **commercial use / monetization requires a separate license** (open a GitHub issue to inquire). See [LICENSE](./LICENSE).
