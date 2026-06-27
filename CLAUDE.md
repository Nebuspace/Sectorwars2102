# Sectorwars2102 — Project Context

**Samantha's persona lives in the output-style** (`.claude/output-styles/samantha.md`), auto-loaded via `.claude/settings.json` (`outputStyle: Samantha`). This file = project context.

> **Note:** this file was formerly the *Self-Improving Development System v3.0.1*. That system is **retired**. Under Samantha Prime: the persona moved to the output-style, the color-mode protocols became **skills** (`.claude/skills/`), and the self-rewriting "CLAUDE.md Evolution Mandate" is **removed** — it caused drift (the file rewriting itself every loop). This is now stable project context: edit it deliberately, not on every iteration.

---

## This Repo

**Sectorwars2102** — a web-based space-trading simulation game. Multi-regional microservices, Docker Compose orchestration.

An instance rooted **here** is an **IMPLEMENTER**: it owns this repo's working tree, builds → proves → reports, and coordinates through `./CROSS-CLAUDE.md`. The **ORCHESTRATOR** runs from the parent workspace root (`/Users/mrathbone/github/Nebuspace/`), sees all sibling repos, issues work orders, and verifies finished work. Claude Code auto-loads `CLAUDE.md` up the directory tree, so when this repo sits under `…/Nebuspace/` the parent **`Nebuspace/CLAUDE.md`** (the full coordination spec) is already in your context — read it for the complete protocol.

**Services / stack:**
- `gameserver` — FastAPI · Python · Poetry · Alembic (Postgres migrations)
- `player-client` — React · TypeScript · npm (the cockpit)
- `admin-ui` — React · TypeScript · npm
- Postgres · Redis · Nginx — orchestrated via Docker Compose

---

## Execution Environment — Read This Before Running Anything

The full stack (FastAPI + Postgres + Redis + Admin UI + Player Client + Nginx) is **NOT** meant to run on the human's MacBook. The Mac is for code editing and lightweight tooling; the full Docker stack runs on a separate remote Linux host reached via Tailscale.

**❌ DO NOT, on the Mac:**
- Run `docker build` / `docker run` / `docker compose up`, or anything that starts/builds containers locally. Docker on the Mac alongside Claude Code throttles the CPU to ~20%.
- Assume a "GCP VM" is the target. An older revision referenced one; that is **outdated** — do not act on it.

**✅ DO, on the Mac:**
- Edit code (TS, Python, YAML, Dockerfiles)
- Node-only commands: `npm test`, `npm run build`, `tsc`
- Isolated `pytest` / `ruff` if a local venv exists (rare)
- `git` freely (commits, branches, status, log)

**Where the full stack actually runs:** infrastructure is documented in the human's separate, local-only repo at `~/github/ServerSetup/` (not part of this codebase). Before planning any work that needs the stack actually running, read `~/github/ServerSetup/README.md` and `~/github/ServerSetup/docs/services/sectorwars-hosting.md` for the real topology, SSH access, and runbooks.
- **Dev + stage** — remote Linux host via Tailscale (`tailscale status` confirms connectivity).
- **Prod** — separate colocated bare-metal host.
- **`docker compose` commands run on the remote dev host via SSH**, never locally.

**Git workflow:** `dev` = development, `master` = tested/validated; the remote dev host tracks `dev` via a sync script. (Active feature work currently lives on `feat/living-npc-system`.) Migrations are **additive only** (nullable columns / new tables) without explicit sign-off.

---

## Essential Commands

**Reminder:** every `docker compose` command runs on the **remote dev host via SSH**, not the Mac. See `~/github/ServerSetup/docs/services/sectorwars-hosting.md` for host names + access.

```bash
# Mac-local (safe to run here)
npm test ; npm run build ; tsc                          # Node-only gates
npx playwright test -c e2e_tests/playwright.config.ts   # E2E (targets dev host via Tailscale)

# Remote dev host, via SSH — Docker Compose profiles
docker compose --profile development up -d              # start all services
docker compose --profile development down              # stop all
docker compose --profile development logs -f           # follow logs
docker compose ps / restart <service> / logs <service> # container management

# Database (remote dev host)
docker compose exec gameserver poetry run alembic upgrade head        # apply migrations
docker compose exec gameserver poetry run alembic revision -m "desc"  # new migration
docker compose exec gameserver poetry run alembic current             # status

# Quality gates (remote dev host)
docker compose exec player-client npm run build   # frontend build + typecheck
docker compose exec player-client npm run lint
docker compose exec admin-ui      npm run lint
docker compose exec gameserver    poetry run pytest
docker compose exec gameserver    poetry run ruff check .
```

---

## Proving Standard

`npm run build` / `tsc` / `pytest` passing is **necessary, not sufficient** — it cannot see layout, geometry, overlap, or visual regression. **Prove beyond the gate and report HOW** in your `📋 STATUS`: headless Playwright geometry / computed-style assertions (`e2e_tests/`), RTL/jsdom component tests, rigorous static computed-layout analysis with real numbers, psql/API for data. The Orchestrator is the independent empirical second layer (it holds the single browser MCP); it does **not** edit the working tree or commit source.

---

## Two-Instance Coordination (Implementer view)

Full protocol = the parent **`Nebuspace/CLAUDE.md`** (auto-loaded) + `.samantha/references/coordination-protocol/`. The essentials you need every session:

**Channels:** `./CROSS-CLAUDE.md` = this repo's **mailbox** (dated, direction-tagged, append-only, gitignored, **no secrets ever**). `../ROSTER.md` = cross-repo presence board (Orchestrator is sole writer).

**Bootstrap (every session):**
1. Read `./CROSS-CLAUDE.md` (tail) + `../ROSTER.md`; look for anything addressed to you and any open `🔧 DEPLOY-WINDOW`.
2. Announce presence: post a dated `🛰️ HEADS-UP` to `./CROSS-CLAUDE.md`.
3. **Arm the watcher** (Bash, `run_in_background: true`) — declare your identity so it doesn't trip on your own writes:
   ```bash
   /Users/mrathbone/github/Nebuspace/.claude/watch-coordination.sh \
     --self <YOUR-IDENTITY> --root /Users/mrathbone/github/Nebuspace
   ```
4. **Arm the heartbeat** (Bash, `run_in_background: true`, `dangerouslyDisableSandbox: true`):
   ```bash
   /Users/mrathbone/github/Nebuspace/.claude/heartbeat.sh \
     ./CROSS-CLAUDE.md <YOUR-IDENTITY> 20 300
   ```

Identity = the lane you own (e.g. `player-client`, `gameserver`, `admin-ui`). If you need a name, use the identity-bootstrap handshake in the reference pack (provisional `pending-<uuid>` → Orchestrator assigns → atomic rename). Re-arm watcher + heartbeat each session and each time they self-cap (~6h); re-arm the watcher as your **LAST** action of a wake-cycle, after all mailbox writes. On a `💓 HEARTBEAT` wake: if mid-task, CONTINUE where you left off; if your queue is genuinely empty, re-arm and stand by.

**The 5 rules (disaster prevention):**
1. **Commit only explicit paths** — `git commit -- <your/owned/paths>`. **NEVER `git add -A` / `git add .`** in this shared tree (it sweeps the other instance's in-flight files — has happened). `git pull --rebase --autostash` before every push.
2. **Bracket shared-runtime changes with a DEPLOY WINDOW** — before a gameserver restart or any DB migration, post `🔧 DEPLOY-WINDOW-OPEN`; post `✅ DEPLOY-WINDOW-CLOSED` when health is green. A frontend-only restart in your exclusive lane = a one-line `🛰️ HEADS-UP`, no window.
3. **Stay in your lane; announce before crossing** — edit only owned paths; to touch a shared file (`package.json`, `core/`, shared types) or another lane, post intent and wait for `🤝 ACK`.
4. **Read `./CROSS-CLAUDE.md` before any commit / push / deploy.**
5. **Never write secrets** to any mailbox or doc. Credentials live in `~/github/ServerSetup/`; `CROSS-CLAUDE.md` is gitignored + local-only.

**Message format:** `### <UTC date -u +%FT%TZ> — <FROM> → <TO> — <emoji TAG>` then the body. Tags: `🤝 HANDOFF` · `📋 STATUS` · `❓ DECISION-NEEDED` · `🔧 DEPLOY-WINDOW-OPEN` · `✅ DEPLOY-WINDOW-CLOSED` · `🛰️ HEADS-UP` · `🤝 ACK` · `💓 HEARTBEAT` · `💡 PROCESS-NOTE`. Append-only; never edit another instance's entries; one logical update = one write, made last; re-read after writing to confirm landing. Reply to a work order with `📋 STATUS` → done (SHA + proof) / blocked / `❓ DECISION-NEEDED`. **A push without a logged DONE is silent divergence.**

**Escalation:** the **Orchestrator is the single point of contact with the human.** Route decisions via `❓ DECISION-NEEDED`; don't stall — park the item, build the unambiguous kernel, continue.

**Safety / out-of-bounds:** auth · payments · MFA · admin-gating/RBAC · AI-dialogue/AI-safety → diagnose freely, **get the human's OK before fixing**. No prod. No history rewrite / force-push without sign-off. No new external deps or docker-compose topology changes without sign-off.

**Enforcement hook:** a `PreToolUse` hook (`/Users/mrathbone/github/Nebuspace/.claude/coordination-precommit-hook.sh`, wired in `.claude/settings.json`) fires before any `git commit | push | rebase`: dumps ROSTER + mailbox tail, warns on `add -A` / `add .` / `commit -a`, runs a non-blocking secret scan.

**Process feedback invited.** Post a `💡 PROCESS-NOTE` for recurring friction. The Orchestrator authors + commits protocol changes, and no change ships without both instances' ratification — you **propose**, you don't edit the protocol docs.

---

## Skills (replace the legacy color-modes)

The old BLUE / GREEN / GOLD / VIOLET / RED / AMBER / NEON color-modes are now **skills** in `.claude/skills/`, invoked by name:

| skill | was | purpose |
|---|---|---|
| `diagnose` | BLUE | diagnostic triage — parallel investigation tracks |
| `build` | GREEN | feature-gap resolution, staged |
| `polish` | GOLD | UI/UX polish convergence |
| `spec-check` | VIOLET | spec ↔ code compliance audit |
| `security-review` | RED | security audit & hardening |
| `i18n` | AMBER | translation / localization quality |

Plus `adversarial-review`, `issue`, and the autonomous discover→build→prove flow (was NEON). Samantha routes to the right skill and dispatches the agents (`monk`, `rook`, `mack`, `cipher`, `pixel`, `rosetta`); see the output-style for the dispatch protocol.

---

## Scroll Law — UI Design Principle

A view's **primary action must be visible without scrolling** at 1440×900 (the reference cockpit resolution). When you dock at a station you should *see* the buy/sell desk; when you land you should *see* the colony controls — not scroll to find them. Collapse low-value chrome, minimize non-essential panels (e.g. the docked station-bay windshield auto-minimizes to hand the band to the console), tile rather than stack, present secondary destinations as tabs/cards rather than buried toggles. Reserve scrolling for genuinely long secondary lists (logs, inboxes, hail history) — never for the primary controls a screen exists to provide. If a default view needs scrolling to reach its core action, the layout is wrong; fix the layout, don't accept the scroll.
