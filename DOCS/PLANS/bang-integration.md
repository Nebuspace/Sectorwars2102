# Bang Integration Plan

**Status**: DRAFT — awaiting Max's review
**Owner**: Claude (implementation), Samantha (review)
**Last revised**: 2026-05-31
**Source repos**: `Sectorwars2102` (this repo) + `sw2102-bang` (canonical at v1.3.0)
**Related ADRs**: ADR-0069 (bang owns deterministic content), ADR-0070 (island formations)

---

## Goal

Replace gameserver's Python `GalaxyGenerator` with `sw2102-bang` as the canonical universe generator. Bang produces deterministic Universe JSON; a Python translator persists it into the gameserver's canonical schema in one transaction. The game runs off the canonical DB rows from then on; the JSON is not retained.

This implements the ADR-0069 contract end-to-end and closes the long-standing soft spot where bang has been a CLI-only tool with no integration path.

---

## Architecture Decision (Finalized)

```
[Admin UI: "Generate Galaxy" form]
     │ POST /api/admin/galaxy/jobs  (returns 202 + job_id)
     ▼
[Gameserver: FastAPI BackgroundTasks queues the job]
     │
[Background task — async]
     │ 1. Acquire pg_advisory_lock(GALAXY_GEN_LOCK)
     │ 2. INSERT into bang_generation_jobs (status=GENERATING)
     │ 3. For each region [player_owned, terran_space (300), central_nexus (5000)]:
     │      a. docker run --rm \
     │           -e TW_SEED=<seed> -e TW_SECTORS=<n> -e REGION_TYPE=<r> \
     │           docker.io/drxelanull/sw2102-bang:1.3.0 \
     │           --json-out  # prints Universe JSON to stdout; no DB writes
     │      b. Capture stdout → parse Universe JSON
     │      c. Capture stderr → stream live to job log table
     │ 4. BEGIN single SERIALIZABLE transaction:
     │      - INSERT Galaxy (import_state=GENERATING, bang_version, bang_seed, bang_config_hash, bang_snapshot JSONB)
     │      - For each region:
     │          translator.write_region(universe, region_metadata)
     │      - Wire cross-region warp gates (existing logic)
     │      - UPDATE Galaxy.import_state=READY, stats counters
     │ 5. COMMIT
     │ 6. UPDATE job (status=COMPLETE)
     │ 7. Emit galaxy.imported on realtime bus
     │ 8. Release advisory lock
     ▼
[Admin UI polls /api/admin/galaxy/jobs/{id} for status + warnings + stats]
[Admin UI subscribes to /api/admin/galaxy/jobs/{id}/stream (SSE) for live log]
```

### Key design choices

| Decision | Choice | Why |
|---|---|---|
| Bridge | **`docker run` sidecar** | gameserver Dockerfile already installs Docker CLI; matches existing infra pattern |
| Bang output | **JSON to stdout** (no `bang.*` schema) | Eliminates 4 critical DB findings (schema ownership, orphans, two-writer, GC) |
| Async | **FastAPI BackgroundTasks** | Galaxy gen is infrequent; arq would be over-engineered for one-off ops |
| Image distribution | **DockerHub** (`docker.io/drxelanull/sw2102-bang`) built via GitHub Actions on tag push | DockerHub free tier has no org namespaces; Max's personal account is `drxelanull` |
| Multi-region atomicity | **Single SERIALIZABLE transaction across all 3 regions** | True atomicity; no partial galaxies queryable |
| Concurrency | **`pg_advisory_lock`** held across the whole job | Race-free; second admin gets 409 |
| Wipe semantics | **Hard-delete** | Per Max; no archive table, no recovery window |
| `GalaxyGenerator` (legacy) | **Removed in same PR as bang ships** | No feature flag, no transition period |
| Partial-state behavior | **Admin UI accessible; player traffic blocked (503)** | Per Max |
| Preview | **Ephemeral** (no job row written for preview) | Saves audit-table noise |
| Audit trail | **Persist `bang_snapshot` JSONB in Galaxy row** | Reproducibility + version-debug without staging schema |

### What does NOT change (out of scope)

- Cross-region warp gate wiring (stays in gameserver per ADR-0069 §52)
- NPCCharacter runtime materialization (gameserver scheduler, unchanged)
- Region.faction_influence values (gameserver-side per ADR-0069 §50)
- Region.owner, governance_type, language_pack, aesthetic_theme (operator state, gameserver-side)
- `region-manager` service (unchanged)

---

## Subagent Worker Strategy

The implementation phases use subagent workers wherever work is **bounded, independent, and contained to a single file or service**. Each subagent gets a tight scope, a contract from this plan, and a definition of done. Claude (main thread) orchestrates and merges.

### Subagent roles

| Role | When used | Tools | Scope size |
|---|---|---|---|
| **Schema Mapper** | Phase 1 pre-work | Read-only | One artifact: a field-by-field map of bang's Universe → gameserver canonical |
| **Translator Author** | Phase 1 implementation | Read/Edit | One Python file: `bang_import_service.py` (~700 LOC) |
| **Job Model Author** | Phase 1 implementation | Read/Edit + Alembic | Two files: Alembic migration + `bang_generation_jobs` model |
| **Dockerfile Author** | Phase 2 | Read/Edit | One Dockerfile + GHCR auth wiring |
| **Form Author** | Phase 3 | Read/Edit | 2-3 React components: form + tiered config |
| **History+Log Author** | Phase 3 | Read/Edit | 2 React components: history table + SSE log panel |
| **bang CLI Author** | Phase 0 | Read/Edit in sw2102-bang repo | bang's `cli.ts` + Dockerfile + package.json |
| **Test Author** | Phase 4 | Read/Edit | e2e Playwright tests + pytest unit tests |
| **Cutover Author** | Phase 4 | Read/Edit | Remove `GalaxyGenerator`; migrate tests |

### Subagent invocation pattern

Each subagent is given:
1. A **link to this plan** for context
2. A **specific phase + role** they own
3. A **definition of done** (tests pass / typecheck clean / specific behavior verified)
4. **DO NOT TOUCH** list (other agents' files)
5. **Commit instructions** (branch, message format)

Claude (main thread) reviews subagent commits before merging.

### Two safeguards

1. **Always run at least one subagent in parallel with a hostile reviewer subagent** when the implementation has cross-cutting impact (e.g., translator touches half the gameserver schema). The reviewer's job: find what the implementor missed.
2. **No subagent commits to `master` directly.** All commits to feature branch `feat/bang-integration` first; Claude reviews; Max merges to dev.

---

## Phase 0 — Prep work (parallel tracks)

Two independent prep tracks run in parallel. Both must complete before Phase 1.

---

## Phase 0-A — Bang-side prep (sw2102-bang repo)

**Estimated effort**: ~150 LOC + Dockerfile changes
**Subagent**: bang CLI Author (1 worker)
**Branch**: `feat/integration-stdout-mode` in sw2102-bang

### Deliverables

1. **Bump `package.json` version to `1.3.0`** (currently `1.2.0` — version drift caught by infra reviewer)
2. Add `--json-out` flag to `src/cli.ts`: when set, print compact Universe JSON to stdout (no file write)
3. Add `--region-type <player_owned|terran_space|central_nexus>` flag
4. Add `--config-json <path|->` flag: accepts a JSON config blob from file or stdin (for richer parameter passing than env vars)
5. Add `--validate-only` flag: emits warnings + stats, no JSON body (for preview)
6. Switch `Dockerfile.generator` runtime base from `node:20-alpine` to `node:20-slim` (Debian/glibc compatibility with anything downstream)
7. Add structured JSON-line stderr: `{"ts":"...","level":"warn","code":"BUBBLE_FALLBACK","msg":"...","data":{...}}`
8. Publish to GHCR: `docker.io/drxelanull/sw2102-bang:1.3.0` (multi-arch via buildx)
9. Update bang's README with the new flags

### Definition of done

- `node dist/cli.js --seed 42 --sectors 1000 --json-out` prints valid Universe JSON to stdout (verifiable with `jq`)
- `node dist/cli.js --seed 42 --sectors 1000 --region-type central_nexus --json-out | jq '.totalSectors'` returns `5000`
- `node dist/cli.js --seed 42 --sectors 1000 --validate-only` prints `{"warnings":[...],"stats":{...}}` and exits 0/2
- Docker image runs on x86_64 Linux without segfault (validated by GitHub Actions CI, not locally)
- DockerHub image is pullable from interstitch (`docker pull docker.io/drxelanull/sw2102-bang:1.3.0`)
- 269/269 tests still pass in bang

---

## Phase 0-B — Stage tunnel + OAuth callback (interstitch + Cloudflare + GitHub)

**Estimated effort**: ~15 min of Max's hands-on time across Cloudflare dashboard + GitHub OAuth registration + SSH commands on interstitch
**Owner**: Max (Claude provides playbook; can't sudo on interstitch or auth to CF dashboard from this session)
**Why now**: Per Max's call, the integration must exercise production-shape OAuth callback URLs from day one. Localhost-shortcut callbacks would let bugs hide until later.

### Deliverables

1. **Cloudflared installed on interstitch** as a systemd unit (one-time, ~5 min). Pre-existing pattern documented in `~/github/ServerSetup/docs/services/sectorwars-hosting.md` § "Setting Up Cloudflare Tunnel on Interstitch".
2. **Public stage hostname** `sw2102-stage.shouden.us` (per ServerSetup convention) routed via Cloudflare DNS → interstitch tunnel → local `nginx-gateway` service in the dev compose stack.
3. **GitHub OAuth app** registered for the stage environment with callback URL `https://sw2102-stage.shouden.us/auth/github/callback` (exact path depends on gameserver router — verify in `services/gameserver/src/auth/oauth.py`).
4. **Cloudflare Access policy** on the stage hostname restricting auth to Max's email (free tier supports up to 50 users; we'll add invited testers later).
5. **Dev compose stack** updated so `nginx-gateway` accepts traffic from cloudflared on the right port (per ServerSetup convention: 9080 for stage). Internal routing: `/` → player-client, `/admin` → admin-ui, `/api` → gameserver.
6. **Gameserver env vars** in the dev compose set `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `OAUTH_REDIRECT_URI=https://sw2102-stage.shouden.us/auth/github/callback`.

### Playbook for Max

```bash
# 1. On interstitch — install cloudflared
ssh interstitch
sudo dnf install -y cloudflared
cloudflared tunnel login                                # opens browser; saves cert
cloudflared tunnel create interstitch-tunnel
cloudflared tunnel route dns interstitch-tunnel sw2102-stage.shouden.us

# 2. On interstitch — config
sudo tee /etc/cloudflared/config.yml <<'EOF'
tunnel: interstitch-tunnel
credentials-file: /root/.cloudflared/<UUID>.json    # replace with actual UUID

ingress:
  - hostname: sw2102-stage.shouden.us
    service: http://localhost:9080                  # nginx-gateway in dev compose
  - service: http_status:404
EOF
sudo systemctl enable --now cloudflared

# 3. In Cloudflare dashboard (cloudflare.com → Zero Trust → Access)
# Create application: sw2102-stage.shouden.us
# Policy: require email = your address; deny everyone else

# 4. In GitHub (github.com → Settings → Developer settings → OAuth apps)
# Register new app:
#   Name: SectorWars 2102 — Stage
#   Homepage: https://sw2102-stage.shouden.us
#   Callback: https://sw2102-stage.shouden.us/auth/github/callback
# Save client ID + secret to a password manager
```

### Definition of done

- `curl -I https://sw2102-stage.shouden.us` from anywhere returns 200/302 (proves tunnel + Cloudflare Access works for authorized email)
- Opening the URL in a browser triggers Cloudflare Access email auth, then lands on player-client
- A test OAuth flow ending at the GitHub callback URL successfully completes (admin login works end-to-end)

### Resolved decisions (2026-05-31, Max delegated)

| ID | Decision | Rationale |
|---|---|---|
| B1 | Stage hostname is **`sw2102-stage.shouden.us`** | Matches ServerSetup convention |
| B2 | **New stage-only GitHub OAuth app** (not reusing prod) | Different callback URL requires separate app; clean credential isolation |
| B3 | **Cloudflare Access: email-only for Max** at start | Dev phase, not demo phase; easy to expand later |
| B4 | **Single hostname, path-based routing via `nginx-gateway`** | One DNS record, one ingress rule, one OAuth callback URL |

### Phase 1 dependency note

Phase 1 (translator) CAN start before Phase 0-B finishes — translator unit tests use captured bang JSON fixtures, no OAuth needed. But Phase 1 integration tests + Phase 3 admin UI need Phase 0-B done before they can validate against real OAuth.

---

## Phase 1 — Gameserver translator + job model

**Estimated effort**: ~700 LOC translator + ~250 LOC migrations/models + ~200 LOC endpoint changes
**Subagents**: 2 parallel (Job Model Author, Translator Author) — schema map already produced
**Branch**: `feat/bang-integration` in Sectorwars2102
**Prerequisites done**: schema map (`bang-integration-schema-map.md`), legacy GalaxyGenerator audit (`legacy-galaxy-generator-audit.md`), test pattern audit (`test-pattern-audit.md`)

### Resolved schema decisions (locked 2026-05-31)

| # | Decision | Migration impact |
|---|---|---|
| Q1 | 9-commodity wire is canonical per ADR-0062 E-D1; the 9th is `precious_metals` | Phase 1 migration extends `Station.commodities` default + adds `precious_metals` to `COMMODITY_PRICE_RANGES` |
| Q2 | Add `Station.is_spacedock BOOLEAN NOT NULL DEFAULT false` | New column |
| Q3 | NPCRoster Strategy A — stash bang's NPCRoster[] in `Galaxy.bang_snapshot.npc_rosters` JSONB; no relational table | None (uses already-planned audit column). **Follow-up ticket recorded: "Implement NPC relational infrastructure".** |
| Q4 | `Planet.owner_id` is already UUID; direct map from `bang.Planet.ownerId` | None |
| Q6 | Extend Postgres enum `special_formation_type` with `LOST_SECTOR`, `LOST_CLUSTER`, `ARCHIPELAGO` | Enum migration |

### Phase 1A — (deleted; schema map already produced in pre-Phase-1 research)

### Phase 1B — Job Model + Schema Migrations Author (independent of 1C)

Owns:
- **Alembic migration `bang_generation_jobs` table**: `id UUID PK, admin_user_id UUID FK, status ENUM(PENDING|RUNNING|COMPLETE|FAILED), params_json JSONB, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ NULL, warnings_json JSONB, duration_ms INTEGER NULL, error_message TEXT NULL, log_text TEXT NOT NULL DEFAULT ''`
- **SQLAlchemy model + Pydantic schemas** for `BangGenerationJob`
- **Alembic migration `galaxy_audit_columns`**: adds `import_state ENUM(GENERATING|READY|FAILED)`, `bang_version VARCHAR`, `bang_seed BIGINT`, `bang_config_hash VARCHAR(64)`, `bang_snapshot JSONB`, `generation_warnings JSONB` to `Galaxy`
- **Alembic migration `bang_schema_decisions`** (new, per Q1/Q2/Q6):
  - Add `Station.is_spacedock BOOLEAN NOT NULL DEFAULT false`
  - Extend `Station.commodities` JSONB default to include `precious_metals` block (use same shape as other commodities, target band 80-180 cr/unit per ADR-0062 E-D1)
  - Add `precious_metals` row to `COMMODITY_PRICE_RANGES` table (or equivalent — confirm exact name)
  - Extend Postgres enum `special_formation_type` with values `LOST_SECTOR`, `LOST_CLUSTER`, `ARCHIPELAGO`
- **Orphan recovery**: startup hook scans `bang_generation_jobs` for `status=RUNNING AND started_at < now() - 5 min`, marks `FAILED` with error_message="orphaned at startup"
- **Player-traffic guard middleware**: refuses non-admin routes with 503 when `Galaxy.import_state != READY`. Single middleware checking once per request via a cached lookup (don't query per request)

### Phase 1C — Translator Author (depends on Schema Mapper output)

Owns `services/gameserver/src/services/bang_import_service.py`:

```python
class BangImportService:
    def invoke_bang(self, config: BangConfig, timeout: int) -> ParsedUniverse:
        """Subprocess docker run; capture stdout JSON + stderr live."""
        
    def validate_only(self, config: BangConfig) -> ValidationReport:
        """Preview: subprocess with --validate-only, no JSON body."""
        
    def translate(self, universes: dict[RegionType, ParsedUniverse], 
                  region_metadata: dict) -> InsertPlan:
        """Pure function: build insert specs. No DB writes."""
        
    def apply(self, plan: InsertPlan, session: AsyncSession) -> Galaxy:
        """Atomic write: one transaction, all 3 regions + warp gates + stats."""
        
    async def run_generation_job(self, job_id: UUID, params: BangConfig):
        """The full async job: lock → invoke 3× bang → translate → apply → commit."""
```

Translator MUST:
- Hold a single transaction across all 3 regions
- Apply gameserver-only state (faction influence, Region.owner, etc.) in the same transaction
- Use `INSERT … ON CONFLICT DO NOTHING` for idempotency safety on sector_number UUIDs
- Log per-region progress to `bang_generation_jobs.log_text`
- Validate Universe.version against pinned `BANG_VERSION` ENV; fail loudly on mismatch
- Run gameserver's own Phase 13 validators AFTER bang's 102 rules; if anything fails, raise and roll back

### Phase 1D — Endpoint changes (Claude main thread, after 1B+1C land)

`services/gameserver/src/api/routes/admin.py`:
- `POST /api/admin/galaxy/jobs` → 202 with `job_id`, queues `BackgroundTasks(run_generation_job)`
- `POST /api/admin/galaxy/preview` → calls `invoke_bang` with `--validate-only`, returns stats + warnings inline
- `GET /api/admin/galaxy/jobs/{id}` → status + warnings + duration + final stats
- `GET /api/admin/galaxy/jobs/{id}/stream` → SSE stream of stderr lines
- `DELETE /api/admin/galaxy/{id}` → hard-delete (cascade); requires confirm-by-typing-name header
- Existing `POST /api/admin/galaxy/generate` → marked deprecated; kept temporarily; calls new path internally; will be removed in Phase 4

### Definition of done (Phase 1)

- Schema map document committed, reviewed by hostile reviewer subagent
- Translator unit tests pass against bang JSON fixtures (one per region type, generated via `node dist/cli.js`)
- Integration test: full 3-region generation completes, all canonical tables populated, `Galaxy.import_state=READY`
- Player traffic to non-admin routes returns 503 when `import_state != READY`
- Concurrent job request returns 409 (advisory lock works)
- Worker-killed-mid-job recovery: startup hook marks orphans `FAILED`
- Hostile reviewer subagent finds no critical issues unaddressed

---

## Phase 2 — Container & registry plumbing

**Estimated effort**: ~60 LOC Dockerfile + ~30 LOC CI
**Subagent**: Dockerfile Author (1 worker)
**Branch**: same as Phase 1

### Deliverables

1. **Modify `services/gameserver/Dockerfile`**: gameserver image already has Docker CLI (line 15) — keep. No multi-stage bang COPY needed since we use `docker run`.
2. **Add `BANG_VERSION` ARG/ENV** to gameserver Dockerfile and docker-compose.yml. Verify at startup that the image is pullable.
3. **Add `tini` ENTRYPOINT** to gameserver image for proper signal forwarding (zombie reaping of bang subprocesses)
4. **docker-compose.yml updates**:
   - Add `BANG_VERSION=1.3.0` env var to gameserver service
   - Add a `gh auth setup-git`-style init for GHCR pull during dev (Codespace handles this)
   - Document that production deploys pull from GHCR
5. **Add Codespaces post-create hook** to pre-pull the bang image so first-generate doesn't wait on `docker pull`
6. **CI changes** (Sectorwars2102 repo):
   - Add a CI step that asserts `BANG_VERSION` env matches the gameserver's expected version
   - Add a smoke test: `docker run docker.io/drxelanull/sw2102-bang:${BANG_VERSION} --seed 42 --sectors 100 --json-out | jq .version` returns `1.3.0`

### Definition of done

- `docker-compose --profile development up` succeeds with bang image pre-pulled
- Codespace can pull `docker.io/drxelanull/sw2102-bang:1.3.0` (DockerHub is public or Codespace has docker login creds in secrets)
- bang subprocess is reaped properly when gameserver container is `docker stop`'d
- Version mismatch is caught at startup, not first invocation

---

## Phase 3 — Admin UI

**Estimated effort**: ~800 LOC across 4-5 components
**Subagents**: 2 parallel (Form Author, History+Log Author)
**Branch**: same as Phase 1

### Phase 3A — Form Author

Owns:
- New `GalaxyGenerationForm.tsx` with **three tiers**:
  - **Common**: seed (with copy/regenerate), regionType, total sectors
  - **Advanced (collapsed)**: federation/border/frontier %, region distribution
  - **Expert (behind "Show developer options")**: maxWarps, oneWayWarps, validator strictness toggle
- **Preview button**: POSTs to `/api/admin/galaxy/preview`, displays stats card (diameter, cluster maxWarps histogram, formation counts by type, validator pass count, warnings categorized)
- **Commit button**: POSTs to `/api/admin/galaxy/jobs`, transitions to job-tracking view
- **bang error code → friendly message map** (translation table): `B-040 → "Generated galaxy is too fragmented (X% reachable). Try a different seed."`
- Wipe galaxy dialog: requires typing exact galaxy name to confirm permanent deletion

### Phase 3B — History+Log Author

Owns:
- `GalaxyGenerationHistory.tsx`: table backed by `/api/admin/galaxy/jobs` (paginated). Columns: date, admin, seed, bang version, region count, warning counts (by category), duration, status. Per-row "Regenerate with same seed" button.
- `GenerationLogPanel.tsx`: SSE-streamed log with auto-scroll. Syntax-highlights warning categories (TOPOLOGY_RESCUE / EMISSION_UNDERTARGET / HEURISTIC_FALLBACK / etc.). Pause/resume scroll. "Copy diagnostic info" button for raw English text.
- `GalaxyOverviewHeader.tsx` (extends existing universe overview): shows `bang_version`, `bang_seed`, diameter, island %, cluster count. Warns if current bang version differs from the version that generated the active galaxy.

### Definition of done (Phase 3)

- Admin can: preview a seed → see stats → discard → preview different seed → commit
- Admin can: monitor a job in progress with live SSE log
- Admin can: review past generations and re-run any of them
- Admin can: wipe galaxy (with confirm dialog)
- All UI strings in i18n keys (admin UI uses i18next)
- All bang error codes mapped to user-friendly messages
- Hostile reviewer subagent confirms no UX gaps from the original review

---

## Phase 4 — Cutover

**Estimated effort**: ~200 LOC removal + ~400 LOC test updates
**Subagents**: 2 parallel (Test Author, Cutover Author)
**Branch**: same as Phase 1

### Phase 4A — Cutover Author

Owns:
- Delete `services/gameserver/src/services/galaxy_service.py:GalaxyGenerator`
- Delete the existing auto-generation logic in `admin.py:1001` (the part that creates Terran Space + Central Nexus inline) — bang now does this
- Remove deprecated `POST /api/admin/galaxy/generate` endpoint or alias it to the new path with a deprecation warning header
- Update any direct callers of `GalaxyGenerator` (search the codebase)

### Phase 4B — Test Author

Owns:
- **e2e Playwright tests**:
  - `e2e/galaxy_generation_full.spec.ts`: preview → commit → verify player can spawn
  - `e2e/galaxy_iteration.spec.ts`: preview seed 42, preview seed 43, preview seed 44, commit 43
  - `e2e/galaxy_wipe_regenerate.spec.ts`: full wipe + new generate
  - `e2e/galaxy_concurrent_admins.spec.ts`: two admins simultaneously; first wins, second gets 409
  - `e2e/galaxy_partial_state.spec.ts`: simulate worker kill mid-job → orphan recovery on next startup
- **Pytest unit tests**: translator against JSON fixtures (4 sample bang outputs: small player_owned, large player_owned, terran_space, central_nexus)
- **Pytest unit tests**: idempotency (re-running translator with same input produces same canonical rows)
- **Pytest unit tests**: failure modes (bang non-zero exit, JSON parse error, schema version mismatch)

### Definition of done (Phase 4)

- All Phase 4A removals committed; no dangling references to deleted code
- All Phase 4B tests pass (target ≥90% coverage of `bang_import_service.py`)
- Existing gameserver test suite still passes (`poetry run pytest`)
- Existing admin-ui tests still pass (`npm run build` + Playwright)
- Single PR ready for merge to `dev`

---

## Failure Mode Matrix

| Failure | Effect | Recovery |
|---|---|---|
| Bang fails validation in preview | Stats card shows "validation failed" + warnings | Admin tweaks seed; no DB change |
| Bang non-zero exit during commit | Job marked FAILED with stderr | Admin retries or wipes |
| Bang subprocess crash mid-run | Same as non-zero exit | Same |
| JSON parse error | Job FAILED; raw stdout logged | File bug against bang version |
| Translator transaction conflict (advisory lock held by another admin) | 409 immediately | Wait for other job to finish |
| Translator validation failure (gameserver Phase 13) | Transaction rolls back; Galaxy.import_state never set to READY | Admin retries with same or different seed |
| Worker killed mid-job (OOM, container restart, signal) | Job left in RUNNING status | Startup orphan scan marks FAILED after 5 min |
| DockerHub pull fails (network, auth) | Job FAILED on first docker run | Admin retries; ops checks DockerHub auth |
| Bang image version mismatch | Caught at startup (smoke test); container won't start | Update `BANG_VERSION` env or rebuild gameserver image |
| Player traffic during partial state | 503 response from middleware guard | Wait for galaxy to reach READY state |

---

## Open Questions Log

These need Max's input before specific subagent dispatches:

| ID | Question | Blocking which phase | Default if not answered |
|---|---|---|---|
| OQ-1 | ~~DockerHub repo name~~ | ~~Phase 0~~ | RESOLVED: `drxelanull/sw2102-bang` (personal DockerHub namespace, free tier has no orgs) |
| OQ-2 | DockerHub repo visibility: public or private? | Phase 0/2 | Affects whether Codespaces/CI need `docker login` or can pull anonymously |
| OQ-3 | Secrets scope: per-repo or org-level (Team upgrade)? | Phase 0 workflow | Per-repo (sidesteps Free-org "Selected repositories" restriction) |
| OQ-3 | Player-traffic 503 — single middleware or per-route guard? | Phase 1B | Single middleware that checks `Galaxy.import_state` once per request |
| OQ-4 | Should preview RUNS show in history table for audit? | Phase 3B | No (ephemeral; only commits in history) |
| OQ-5 | Translator: prefer `INSERT … ON CONFLICT` or pre-flight UNIQUE checks? | Phase 1C | `ON CONFLICT DO NOTHING` for retry safety |

---

## Decision Log

| Date | Decision | Why | Decider |
|---|---|---|---|
| 2026-05-31 | Use `docker run` sidecar pattern, not multi-stage Dockerfile COPY | gameserver Dockerfile already installs Docker CLI; matches existing infra | Max |
| 2026-05-31 | Drop `bang.*` schema; bang prints JSON to stdout | Eliminates 4 critical DB findings | Reviewer synthesis |
| 2026-05-31 | FastAPI BackgroundTasks (not arq) | Galaxy gen is infrequent; no task queue installed | Max + reviewer |
| 2026-05-31 | Hard-delete galaxy on wipe | Per Max | Max |
| 2026-05-31 | Remove Python `GalaxyGenerator` immediately on cutover | Per Max | Max |
| 2026-05-31 | Player traffic blocked during partial state; admin UI allowed | Per Max | Max |
| 2026-05-31 | Single SERIALIZABLE transaction across all 3 regions | Atomicity per DB reviewer #1 | Reviewer synthesis |
| 2026-05-31 | `pg_advisory_lock` for concurrent serialization | Per DB reviewer #3 | Reviewer synthesis |

---

## Acceptance Criteria (whole project)

The integration is "done" when:

1. Fresh Codespace + `docker compose --profile development up` produces a working stack with no galaxy
2. Admin logs in, generates a galaxy via the new form, sees live progress, sees final stats
3. Generated galaxy is identical across two runs of the same `(seed, sectors, regionType)` (determinism verified)
4. Player traffic returns 503 until galaxy reaches READY
5. Admin can preview multiple seeds without dirtying DB
6. Admin can wipe galaxy + regenerate without DB surgery
7. Concurrent admins: one wins, other gets 409
8. Worker crash mid-job: orphan recovery on next startup
9. bang version drift: caught at build, not in prod
10. Python `GalaxyGenerator` is deleted; tests still pass
11. New code has ≥90% test coverage
12. No regressions in existing gameserver / admin-ui tests
13. Documentation (this file + bang README + ADR pointers) is current

---

## Timeline Estimate (calendar)

Optimistic (no surprises):
- **Phase 0**: 0.5 day (bang changes + GHCR publish)
- **Phase 1**: 2-3 days (translator + job model + endpoints; depends on schema map clarity)
- **Phase 2**: 0.5 day (Dockerfile + CI)
- **Phase 3**: 2-3 days (admin UI)
- **Phase 4**: 1 day (cutover + tests)

**Total**: ~6-8 days serial; ~3-4 days with subagent parallelism on Phase 1 + Phase 3

Realistic: add 30-50% for schema-map discovery (the field-by-field bang↔gameserver mapping is the largest unknown).

---

## What I want from Max before starting Phase 0

1. ✅ **Confirmation that this plan is approved** to proceed
2. **GHCR org path** (OQ-1)
3. **Codespaces auth strategy** for image pulls (OQ-2)
4. Anything else worth questioning before I dispatch subagents
