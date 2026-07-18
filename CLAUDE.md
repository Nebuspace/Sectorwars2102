# Sectorwars2102 — Project Context

> **⛔ REMOTE SEAT? READ THIS FIRST — do not skip to the rest of this file.**
> If you are an AI implementer agent that cloned this repository on a machine
> OTHER than the primary developer's Mac, **STOP.** Everything below — heimdall,
> Docker, "arm your monitors," the Two-Instance Coordination section, the
> Execution Environment — assumes the primary-Mac environment and provisioning
> you do NOT have. Following it will only confuse you.
>
> Your ONLY instructions as a remote seat:
> 1. Confirm you cloned the repo and are on the working branch the hub named you.
> 2. Report to the human who launched you: "Remote implementer seat, ready for onboarding."
> 3. Request the private onboarding pack from the hub (orchestrator) and WAIT.
> Do not reach any server, reference any other repository (there is no Nebuspace
> on your machine), or arm any coordination until you have the pack AND your
> machine has been provisioned. The pack — not this file — is your real runbook.

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

**Channels:** Your **own file** is `.samantha/coord/<your-identity>.md` — presence + outbox. Default single-implementer identity is `impl-sectorwars`; on a lane split the proven pair is `impl-gameserver` / `impl-admin-ui` (hub auto-discovers both). Read your file back after every write. You **watch only** `orchestrator.md` — your inbox. Never write into a peer's file.

**Bootstrap (every session):**
1. Read `.samantha/coord/orchestrator.md` in full — catch up on open WOs, decisions, and any open `🔧 DEPLOY-WINDOW`. **First M9 session only:** also read `../ROSTER.gen1-archive.md` + `./CROSS-CLAUDE.gen1-archive.md` to recover in-flight state carried over from the retired protocol (a live enrichment campaign + a GATE-STAGING lane were mid-flight), then proceed on M9.
2. Self-register / refresh your own coord file (role=Implementer, zone=this repo/lane, state=Active). Read it back to confirm the write landed. No pre-assigned name? Identity-bootstrap (`pending-<uuid>` → hub `🤝 ASSIGN-IDENTITY` → adopt) — see the coordination-protocol README.
3. **Arm the coord-monitor + heartbeat** (harness-specific — see below). Confirm with `coord-status.sh` → **BOTH ALIVE**.
4. Post `🤝 ACK` / `🛰️ HEADS-UP` to your own file: "`<identity>` armed in. Watching `orchestrator.md`."

### Arming the inbox — Claude Code vs Cursor (do not mix these up)

`coord-monitor.sh` is a **forever-running process**. Liveness (PID in `.watch-state/<id>/watcher.pid`) is necessary but **not sufficient** — the agent must also be **woken when the script prints**. A background shell with no output→chat bridge is a deaf gap (incident 2026-07-17: monitor advanced past `ASSIGN-IDENTITY` while the Cursor agent never saw it).

**Shared command** (identity = your seat — `impl-sectorwars` / `impl-admin-ui` / etc.):
```bash
/Users/mrathbone/github/Nebuspace/.claude/coord-monitor.sh \
  --identity <your-identity> \
  --dir /Users/mrathbone/github/Nebuspace/.samantha/coord

/Users/mrathbone/github/Nebuspace/.claude/heartbeat.sh \
  --identity <your-identity> --role implementer \
  --dir /Users/mrathbone/github/Nebuspace/.samantha/coord
```
Defaults: heartbeat `--idle-threshold 1200` (20min), `--cadence 300` (5min).

| Harness | How to arm so you get alerted |
|---|---|
| **Claude Code** | **`Monitor` tool**, `persistent: true`, for `coord-monitor.sh`. Heartbeat via Bash `run_in_background: true` (+ `dangerouslyDisableSandbox` if needed). Monitor streams `┃ COORD ▼` lines into chat as events. |
| **Cursor Agent** | **Shell tool**, `block_until_ms: 0` (background), **`required_permissions: ["all"]`**, and **`notify_on_output` required** so stdout wakes this session. Plain `command &` / background-without-notify is **forbidden** — the process will look ALIVE in `coord-status.sh` while the agent stays deaf. |

**Cursor `notify_on_output` (copy these):**
- Monitor — `pattern`: `COORD ▼|HEARTBEAT DOWN|ASSIGN-IDENTITY|HANDOFF|DEPLOY-WINDOW` · `reason`: `Coord inbox peer message` · `debounce_ms`: `5000`
- Heartbeat — `pattern`: `WATCHER-DOWN|exit 42|HEARTBEAT DOWN` · `reason`: `Heartbeat dead-man alert` · `debounce_ms`: `5000`

On a notify wake: read the new tail of `orchestrator.md` (or the emitted block), act, do **not** re-arm a still-ALIVE monitor. Re-arm ONLY the dead one (kill by recorded PID — never `pkill -f`), then `coord-status.sh` → BOTH ALIVE. On `💓 HEARTBEAT` wake: if mid-task CONTINUE; if queue empty, stand by.

**Never** use the retired echo-and-terminate `watch-coordination.sh` as the live inbox.

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

---

## Remote seats

**If you are a remote implementer seat, the ⛔ banner at the top of this file is your instruction — read it.** In brief: you cannot self-configure from this public repository — your identity, the coordination endpoint, connection credentials, and your working branch are deliberately never stored here. Request the private onboarding pack from the hub (orchestrator) and wait until you have it AND your machine has been provisioned. You work on the shared active feature branch the hub names for you, never the default branch. The pack is your real runbook.
