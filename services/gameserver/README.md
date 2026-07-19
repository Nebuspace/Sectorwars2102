# SectorWars 2102 — Game Server

FastAPI backend for SectorWars 2102: the game's REST API, WebSocket
connections, and background schedulers. Python 3.11+, managed with Poetry,
schema migrations via Alembic.

## Layout

- `src/` — application code (`src/main.py` is the FastAPI entrypoint;
  `src/api/`, `src/models/`, `src/services/` follow the usual route/model/
  service split)
- `alembic/` + `alembic.ini` — database migrations
- `tests/` — pytest suite (`tests/unit/`, `tests/integration/`)
- `scripts/` — one-off and maintenance scripts
- `i18n/` — server-side translation strings

## Development

This service does not run standalone on a developer's Mac — the full stack
(Postgres, Redis, this service, the two frontends) runs via Docker Compose
on a remote dev host. See the repo-root `CLAUDE.md` for the actual dev
workflow, SSH access, and the reasoning behind that split.

Local-only, Mac-safe commands:

```bash
poetry install                          # install dependencies
poetry run pytest tests/unit            # DB-free unit tests
poetry run ruff check .                 # lint
```

`poetry run pytest tests/unit` needs five environment variables set
(`JWT_SECRET`, `ARIA_ENCRYPTION_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`,
`DATABASE_URL`) or it fails at collection — `src/core/config.py` requires
them at import time. See `.github/workflows/ci-build-test.yml`'s "Unit
tests (DB-free lane)" step for the exact dummy values CI uses.

`poetry run pytest tests/integration` and `alembic upgrade head` need a live
database and are run on the remote dev host via `docker compose exec
gameserver ...` — see the repo-root `CLAUDE.md` for the exact commands.
