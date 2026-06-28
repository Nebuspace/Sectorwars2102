# Sector Wars 2102

A web-based space trading simulation game featuring a **Multi-Regional Platform** with player-owned territories, sophisticated governance systems, and AI-powered intelligence.

## Overview

Sector Wars 2102 is an innovative turn-based space trading game that transforms the traditional single-galaxy experience into a **Multi-Regional Universe**. Players can own and govern entire regional territories, participate in democratic governance, manage economic policies, and engage in complex diplomatic relations. The game features a Central Nexus hub (5000 sectors) connecting all regional territories, sophisticated AI-powered systems, and monetization through regional ownership subscriptions.

### AI-Powered Features

#### ARIA Trading Intelligence System
**ARIA** (Autonomous Resource Intelligence Assistant) — the trading companion:
- **Personalized Recommendations**: AI learns your trading style and provides tailored suggestions
- **Market Predictions**: Advanced algorithms forecast commodity price movements
- **Route Optimization**: AI calculates optimal multi-sector trading paths
- **Risk Assessment**: Early warnings for dangerous sectors and market conditions
- **Real-time Learning**: Continuously improves based on your feedback and success

#### AI-Powered First Login Experience
**Dynamic Narrative Dialogue** — immersive AI-driven character interactions:
- **Adaptive Conversations**: Guard NPCs respond dynamically using LLM AI (Anthropic Claude/OpenAI GPT)
- **Contextual Questioning**: Questions adapt based on your responses and claimed ship type
- **Intelligent Analysis**: AI analyzes persuasiveness, confidence, and consistency in real-time
- **Natural Language Processing**: Free-form text input with sophisticated response understanding
- **Seamless Fallback**: Robust rule-based system ensures 100% reliability when AI is unavailable

---

## Architecture

The project uses a microservices architecture orchestrated with Docker Compose and profile-gating.

### Services

| Service | Profile(s) | Role |
|---|---|---|
| `database` | `development`, `default` | PostgreSQL — single-server dev database |
| `central-nexus-db` | `multi-regional`, `production` | PostgreSQL — Central Nexus |
| `redis-cache` | `development`, `default`, `multi-regional` | Cache / session store |
| `redis-nexus` | `multi-regional`, `production` | Nexus-scoped Redis |
| `gameserver` | `development`, `default` | FastAPI game API (single-server dev) |
| `central-nexus-server` | `multi-regional`, `production` | FastAPI — Central Nexus service |
| `player-client` | `development`, `default`, `multi-regional` | React cockpit (Vite) |
| `admin-ui` | `development`, `default`, `multi-regional` | React admin interface (Vite) |
| `nginx-gateway` | `development`, `default`, `multi-regional` | Reverse proxy / gateway |
| `region-manager` | `development`, `default`, `multi-regional` | Regional orchestration service |
| `prometheus` / `grafana` | `monitoring`, `production` | Observability stack |
| `regional-server-template` | `regional-template` | Regional shard template |

> **A bare `docker compose up` starts nothing** — every service is profile-gated. Always pass `--profile`.

### Component Overview

1. **Game API Server** (`gameserver` / `central-nexus-server`): Core backend — all game logic, database operations, API endpoints
2. **Player Client**: The cockpit — React 19 + Vite 8, three.js / react-three-fiber for 3D visualization, chart.js + D3.js for analytics
3. **Admin UI**: Administrative interface — React + TypeScript, D3.js dashboards
4. **nginx-gateway**: Routes player-client, admin-ui, and API traffic
5. **region-manager**: Orchestrates multi-regional shards

---

## Technical Stack

- **Backend**: FastAPI (Python 3.11), SQLAlchemy 2, Alembic, Poetry
- **Database**: PostgreSQL 15
- **Cache**: Redis
- **Authentication**: JWT-based
- **Frontend**: React 19 + TypeScript, Vite 8
- **3D Visualization**: three.js, react-three-fiber (player cockpit)
- **Charts / Analytics**: chart.js, D3.js (admin UI)
- **Testing**: Pytest (backend), Playwright (E2E), React Testing Library
- **Containerization**: Docker + Docker Compose (profile-based)

---

## Environments

The full Docker stack does **not** run on a developer's Mac — it runs on a remote Linux host accessed via Tailscale.

| Environment | Location | Notes |
|---|---|---|
| **Dev / Stage** | Remote Linux host via Tailscale | Tracks active feature branch; Vite hot-reloads client |
| **Production** | Separate colocated bare-metal host | |

Infrastructure details, SSH access, and runbooks live in `~/github/ServerSetup/` (local-only, not part of this repo). See `ServerSetup/docs/services/sectorwars-hosting.md` for host names and access.

---

## Quick Start

### Prerequisites

- **Mac / local machine**: Node.js 20+ (22 LTS recommended), Python 3.11+, git
- **Remote dev host**: Docker + Docker Compose, accessible via Tailscale

> **Do not run `docker build`, `docker run`, or `docker compose` on your Mac.** The full stack runs on the remote Linux dev host. Running Docker locally alongside Claude Code throttles CPU to ~20%.

### 1. Clone the repo

```bash
git clone https://github.com/Nebuspace/Sectorwars2102.git
cd Sectorwars2102
```

### 2. Start the stack on the remote dev host (via SSH)

```bash
# SSH into the dev host (see ServerSetup/docs/services/sectorwars-hosting.md for host name)
ssh <dev-host>

# In /data/sectorwars-dev (or the configured project root):
docker compose --profile development up -d
```

This brings up: `database`, `redis-cache`, `gameserver`, `player-client`, `admin-ui`, `nginx-gateway`, `region-manager`.

### 3. Apply database migrations (remote dev host)

```bash
docker compose exec gameserver poetry run alembic upgrade head
```

### 4. Access services

With Tailscale connected, reach the dev host's exposed ports:

- **Player Client**: `http://<dev-host>:3000`
- **Admin UI**: `http://<dev-host>:3001`
- **Game API**: `http://<dev-host>:8080`
- **API Docs**: `http://<dev-host>:8080/docs` (Swagger UI — available when `DEBUG=true`)

### Compose profiles

| Profile | Use case |
|---|---|
| `development` / `default` | Standard dev — single gameserver + supporting services |
| `multi-regional` | Multi-regional dev with Central Nexus |
| `production` | Production topology |
| `monitoring` | Add Prometheus + Grafana |
| `regional-template` | Spin up a regional shard template |

---

## Mac-Local Development (code + tests only)

Mac-safe commands — these do **not** require Docker:

```bash
# Player Client — type-check (Vite/esbuild does NOT type-check on build)
cd services/player-client
npm install
npx tsc --noEmit      # always run before pushing frontend work
npm run build         # build artifact

# Admin UI
cd services/admin-ui
npm install
npx tsc --noEmit
npm run build

# Gameserver — linting (requires a local venv or Poetry)
cd services/gameserver
poetry install
poetry run ruff check .
poetry run pytest     # unit tests; integration tests require the running stack
```

> **Note**: `npm run build` (Vite) does not type-check. Run `npx tsc --noEmit` explicitly before deploying — type errors and undefined-name bugs ship silently otherwise.

---

## Testing

### Backend (Gameserver)

```bash
# Remote dev host — full test suite (requires running stack)
docker compose exec gameserver poetry run pytest

# Mac-local — unit tests only (if local Poetry env is set up)
cd services/gameserver
poetry run pytest
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

- **`master`** — tested, validated code. Primary integration target.
- **`feat/living-npc-system`** — active feature branch (current work).
- **`dev`** — development; the remote dev host sync script tracks this branch.

Migrations are **additive only** (nullable columns or new tables) — no destructive schema changes without explicit sign-off.

---

## Documentation

- **Project context / dev guide**: [`CLAUDE.md`](./CLAUDE.md)
- **Canonical game spec** (public): [`sw2102-docs`](https://github.com/Nebuspace/sw2102-docs) — auto-published to Cloudflare on every push to `main`; this is the primary specification reference
- **In-repo AI specs**: [`AISPEC/`](./AISPEC/) — detailed system specifications; note this directory is currently untracked in git (`AISPEC/` is in the working tree but not committed)
- **E2E test suite**: [`e2e_tests/`](./e2e_tests/)
