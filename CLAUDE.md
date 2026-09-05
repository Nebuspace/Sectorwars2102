# Sectorwars2102 — project context

Web-based space-trading simulation. Multi-regional microservices on Docker Compose.

## Services

| Service | Stack |
|---|---|
| `gameserver` | FastAPI · Python · Poetry · Alembic (Postgres migrations) |
| `player-client` | React · TypeScript · npm — the player cockpit |
| `admin-ui` | React · TypeScript · npm |
| infra | Postgres · Redis · Nginx |

## Execution environment — read before running anything

The full stack does NOT run on this Mac. The Mac is for editing and Node-only
tooling; the stack runs on a remote Linux host reached over Tailscale.

**Never on the Mac:** `docker build` / `docker run` / `docker compose up`, or
anything that starts or builds a container. Docker here throttles the CPU to ~20%.
There is no GCP VM; older docs that mention one are wrong.

**On the Mac:** edit code · run each service's own `npm` scripts (see Commands
below — there are no root-level `test`/`build` scripts) · `git` ·
isolated `pytest`/`ruff` if a local venv exists.

Topology, SSH access and runbooks live in a separate local-only repo:
`~/github/ServerSetup/README.md` and
`~/github/ServerSetup/docs/services/sectorwars-hosting.md`. Dev and stage are the
remote Tailscale host; prod is a separate colocated bare-metal host.

## Commands

```bash
# Mac-local — each frontend service has its own scripts (no root-level ones)
cd services/player-client && npm test ; npm run build ; npm run typecheck
cd services/admin-ui      && npm test ; npm run build ; npm run typecheck
npx playwright test -c e2e_tests/playwright.config.ts   # targets the dev host

# Remote dev host, over SSH — never locally
docker compose --profile development up -d | down | logs -f
docker compose exec gameserver    poetry run alembic upgrade head
docker compose exec gameserver    poetry run alembic revision -m "desc"
docker compose exec gameserver    poetry run pytest
docker compose exec gameserver    poetry run ruff check .
docker compose exec player-client npm run build
docker compose exec player-client npm run lint
docker compose exec admin-ui      npm run lint
```

## Git and schema

`master` is the integration branch. Work-order branches are `wo/<key>` and are
created server-side — switch onto the branch your work order names, never invent
one. This tree is shared by concurrent agents: **commit only explicit paths**
(`git commit -- <your/paths>`), never `git add -A` or `git add .`, and never
`git stash`. `git pull --rebase --autostash` before a push. No force-push, no
history rewrite.

Migrations are **additive only** — nullable columns and new tables — unless a
human signs off on anything else.

## Proving standard

A green `npm run build` / `npm run typecheck` / `pytest` is necessary, not
sufficient: it cannot see layout, geometry, overlap, or visual regression. Prove
beyond the gate and say how: Playwright geometry and computed-style assertions in
`e2e_tests/`, RTL/jsdom component tests, or psql/API reads for data.

## Scroll Law — UI design

A view's primary action must be visible without scrolling at 1440×900, the
reference cockpit resolution. Docking at a station shows the buy/sell desk;
landing shows the colony controls. Collapse chrome, tile rather than stack, use
tabs or cards for secondary destinations. Reserve scrolling for genuinely long
secondary lists such as logs, inboxes and hail history. If a default view must be
scrolled to reach its core action, fix the layout.

## Out of bounds without a human's OK

Auth · payments · MFA · admin gating and RBAC · AI-dialogue and AI-safety code:
diagnose freely, do not fix without sign-off. No production. No new external
dependencies and no docker-compose topology changes without sign-off.
