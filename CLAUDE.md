# Sectorwars2102 — Project Context

**Samantha's persona lives in the output-style** (`.claude/output-styles/samantha.md`), auto-loaded via `.claude/settings.json` (`outputStyle: Samantha`). This file = project context.

> **Note:** this file was formerly the *Self-Improving Development System v3.0.1*. That system is **retired**. Under Samantha Prime: the persona moved to the output-style, the color-mode protocols became **skills** (`.claude/skills/`), and the self-rewriting "CLAUDE.md Evolution Mandate" is **removed** — it caused drift (the file rewriting itself every loop). This is now stable project context: edit it deliberately, not on every iteration.

---

## This Repo

**Sectorwars2102** — a web-based space-trading simulation game. Multi-regional microservices, Docker Compose orchestration.

An instance rooted **here** is an **IMPLEMENTER**: it owns this repo's working tree, builds → proves → reports, and coordinates through its own file in `/Users/mrathbone/github/Nebuspace/.samantha/coord/`. The **ORCHESTRATOR** runs from the parent workspace root (`/Users/mrathbone/github/Nebuspace/`), sees all sibling repos, issues work orders, and verifies finished work. Claude Code auto-loads `CLAUDE.md` up the directory tree, so when this repo sits under `…/Nebuspace/` the parent **`Nebuspace/CLAUDE.md`** (the full coordination spec) is already in your context — read it for the complete protocol.

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

Full protocol = the parent **`Nebuspace/CLAUDE.md`** (auto-loaded) + `.samantha/references/coordination-protocol/README.md`. This is the **M9 STAR-topology** protocol — the essentials for this repo's seat:

**Channels:** `/Users/mrathbone/github/Nebuspace/.samantha/coord/impl-sectorwars.md` is this instance's **own file** — simultaneously its presence entry and its outbox (the default single-implementer identity for this repo). Read it back after every write. You **watch only** `/Users/mrathbone/github/Nebuspace/.samantha/coord/orchestrator.md` — your inbox for handoffs and decisions. Never write to the Orchestrator's file; a message you send is an append to your own file, which its watcher tails. If two Implementers share this repo on disjoint path lanes (the proven `player/gameserver` ↔ `admin-ui` split), each takes its own identity + coord file — `impl-gameserver` / `impl-admin-ui` — in the same coord-dir; the hub auto-discovers both.

**Bootstrap (every session):**
1. Read `.samantha/coord/orchestrator.md` in full — catch up on open WOs, decisions, and any open `🔧 DEPLOY-WINDOW`. **First M9 session only:** also read `../ROSTER.gen1-archive.md` + `./CROSS-CLAUDE.gen1-archive.md` to recover in-flight state carried over from the retired protocol (a live enrichment campaign + a GATE-STAGING lane were mid-flight), then proceed on M9.
2. Self-register / refresh `.samantha/coord/impl-sectorwars.md` (or your assigned lane identity — role=Implementer, zone=this repo/lane, state=Active). Read it back to confirm the write landed.
3. **Arm the coord-monitor** — the persistent streaming inbox. Launch it via the **`Monitor` tool** with **`persistent: true`** (NOT `Bash run_in_background`; that armed the retired echo-and-terminate `watch-coordination.sh`). It streams `orchestrator.md` (your inbox) into the chat as events — no per-message re-arm, no deaf gap. `command`:
   ```bash
   /Users/mrathbone/github/Nebuspace/.claude/coord-monitor.sh \
     --identity impl-sectorwars \
     --dir /Users/mrathbone/github/Nebuspace/.samantha/coord
   ```
4. **Arm the heartbeat** (Bash, `run_in_background: true`, `dangerouslyDisableSandbox: true`):
   ```bash
   /Users/mrathbone/github/Nebuspace/.claude/heartbeat.sh \
     --identity impl-sectorwars --role implementer \
     --dir /Users/mrathbone/github/Nebuspace/.samantha/coord
   ```
   Defaults: `--idle-threshold 1200` (20min idle before a HEARTBEAT auto-posts), `--cadence 300` (5min check interval).
5. Post `🤝 ACK` / `🛰️ HEADS-UP` to your own file: "impl-sectorwars armed in. Watching `orchestrator.md`."

Identity = the lane you own (`impl-sectorwars` by default, or `impl-gameserver` / `impl-admin-ui` on a lane split). If you need a name, use the identity-bootstrap handshake in the reference README (provisional `pending-<uuid>` → Orchestrator assigns → atomic rename). Arm the coord-monitor (persistent, once per session) + the heartbeat; the monitor streams every message so there is **no per-wake-cycle re-arm and no deaf gap** (that discipline was the retired echo-and-terminate watcher). The two mutually-monitor — if either dies its sibling alerts in-chat (heartbeat self-exits `42`; monitor prints `⚠️ HEARTBEAT DOWN`); re-arm ONLY the dead one (monitor via the `Monitor` tool, heartbeat via `Bash run_in_background`), then `coord-status.sh` to confirm BOTH ALIVE. On a `💓 HEARTBEAT` wake: if mid-task, CONTINUE where you left off; if your queue is genuinely empty, stand by (the monitor stays armed — nothing to re-arm).

**The 5 rules (disaster prevention):**
1. **Commit only explicit paths** — `git commit -- <your/owned/paths>`. **NEVER `git add -A` / `git add .`** in this shared tree (it sweeps the other instance's in-flight files — has happened). `git pull --rebase --autostash` before every push.
2. **Deploy windows are hub-mediated** — you cannot broadcast to siblings directly (STAR topology). Need one? Post `🔧 DEPLOY-WINDOW REQUEST → orchestrator` before a gameserver restart or any DB migration; wait for `🔧 DEPLOY-WINDOW-OPEN` before proceeding, and watch for `✅ DEPLOY-WINDOW-CLOSED`. A frontend-only restart in your exclusive lane = a one-line `🛰️ HEADS-UP`, no window.
3. **Stay in your lane; announce before crossing** — edit only owned paths; to touch a shared file (`package.json`, `core/`, shared types) or another lane, post intent and wait for `🤝 ACK`.
4. **Read `orchestrator.md`'s tail before any commit / push / deploy.**
5. **Never write secrets** to any coord-dir file. Credentials live in `~/github/ServerSetup/`; every coord-dir message is version-controlled and effectively public within the team.

**Message format:** `### <UTC date -u +%FT%TZ> — impl-sectorwars → orchestrator — <emoji TAG>` then the body, appended to your own file. Tags: `🤝 HANDOFF` · `📋 STATUS` · `❓ DECISION-NEEDED` · `🔧 DEPLOY-WINDOW REQUEST` · `🛰️ HEADS-UP` · `🤝 ACK` · `💓 HEARTBEAT` · `💡 PROCESS-NOTE`. Append-only; never edit another instance's entries; one logical update = one write, made last; re-read after writing to confirm landing. Reply to a work order with `📋 STATUS` → done (SHA + proof) / blocked / `❓ DECISION-NEEDED`. **A push without a logged DONE is silent divergence.**

**Escalation:** the **Orchestrator is the single point of contact with the human.** Route decisions via `❓ DECISION-NEEDED`; don't stall — park the item, build the unambiguous kernel, continue.

**Safety / out-of-bounds:** auth · payments · MFA · admin-gating/RBAC · AI-dialogue/AI-safety → diagnose freely, **get the human's OK before fixing**. No prod. No history rewrite / force-push without sign-off. No new external deps or docker-compose topology changes without sign-off.

**Enforcement hook:** a `PreToolUse` hook (`/Users/mrathbone/github/Nebuspace/.claude/coordination-precommit-hook.sh`, wired in `.claude/settings.json`) fires before any `git commit | push | rebase`: dumps the coord-dir state, warns on `add -A` / `add .` / `commit -a`, runs a non-blocking secret scan.

**Process feedback invited.** Post a `💡 PROCESS-NOTE` for recurring friction. The Orchestrator authors + commits protocol changes, and no change ships without unanimous active-member ratification — you **propose**, you don't edit the protocol docs.

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

---

## Status questions = proceed (Max, 2026-07-15)

When the human asks a status question about an unfinished next step — especially **"did you deploy to Heimdall?"**, "is this live?", "did you push?", "is it on stage?" — treat it as a **strong hint to do that step now**, not as a yes/no quiz.

**Why:** he is asking because the expectation is that the work should already be (or immediately become) live on stage. Answering "no — want me to?" and stopping is the wrong move; acknowledge briefly if needed, then **proceed**.

**Default for UI / player-client work after a local fix:** sync the changed files to Heimdall (`scp`/`rsync` into `/opt/sectorwars-dev/...` so Vite HMR picks them up, or the bundle→ff-merge path when committing). Frontend-only = `🛰️ HEADS-UP`, no deploy window. Gameserver restart / migration still needs a hub-mediated window.

**Still ask first** only when the action is truly gated: commit (unless he already said commit), push to a shared remote he didn't ask for, prod, force-push, destructive migration, auth/payments/MFA/admin-gating/AI-safety fixes.
