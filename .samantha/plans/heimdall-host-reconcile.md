# heimdall host reconcile — scoped proposal

**Status:** proposed, awaiting Max. Nothing executed. DB untouched at `a7c4e91b2d08`.
**Author:** impl-sectorwars · 2026-08-03
**Origin:** the Max-ruled RBAC chain could not be applied — see "What blocked it" below.

---

## The finding

Applying Max's RBAC ruling failed before it started, because alembic on the dev host cannot build a
revision map at all:

```
$ ssh heimdall docker exec sectorwars-gameserver poetry run alembic current
KeyError: 'c4e17b2a95df'
```

`d7e4a1b9c2f0` is deployed; **both of its ancestors are not**. A dangling `down_revision` breaks the
whole map, so `current`, `heads`, and `upgrade` all fail. **Migrations on dev have been entirely
non-functional** since that partial deploy landed, and nothing surfaced it because nothing routinely
runs `alembic current`.

That is the symptom. The cause is drift:

| measure | value |
|---|---|
| host HEAD | `f8e46b1e` on `feat/expeditions-vista` |
| Mac tip | `bb97b994` (same branch) |
| **commits behind** | **115** |
| fast-forwardable? | **yes** — `f8e46b1e` is an ancestor of tip |
| migration files | host 111 · tip 113 |
| host `models/player.py` declares K2 cols? | **no** (tip: yes) |
| uncommitted files on host | 45 |

Rule 6's host-drift ceiling is **10 commits / 48h**. We are at **11.5×** it, and the ceiling's own
remedy — auto-create a reconcile ticket at the next quiet point — never fired. That is a second,
separate infra gap.

## Why I refused the quick fix

Hand-copying the two missing migrations would create the K2 columns against a host ORM that does not
declare them — the exact DB/code mismatch the post-verify ORM check exists to catch. And
cherry-picking 3 files out of 115 commits **is the practice that caused this**. A partial deploy is
not repaired by another partial deploy.

## Content-neutrality checklist — PASSED

The mandatory READ-ONLY check before any `reset --hard`. Run against all 45 dirty files:

| result | count | meaning |
|---|---|---|
| byte-identical to tip | **35** | hot-deployed copies of committed code |
| differ from tip, but blob **exists in git history** | **10** | deployed from an older commit |
| unique content, in no commit | **0** | — |

Every one of the 10 divergent files resolves to a real commit (`358b4336` precious-metals,
`b42e90ca` eslint sweep, `d3876217` HUD CSS, `37cb4494`/`11a5836f`/`b388b180` admin-ui). The
precious-metals trio is *older* than tip, confirming the host is behind rather than ahead.

**Conclusion: no unique work exists on heimdall. A `reset --hard` to tip destroys nothing that is
not already in git.** This is the load-bearing result; without it the reconcile would be destructive.

## Runtime shape

`sectorwars-gameserver` **bind-mounts** the checkout:

```
/opt/sectorwars-dev/services/gameserver -> /app (bind)
```

So the reset updates the container's code immediately — **no image rebuild**, only a restart to
reload Python. Container file count (111) matches the host, confirming the mount is live.

## Proposed sequence — needs its own deploy window

1. `git fetch origin feat/expeditions-vista`; re-confirm ancestor (no divergence).
2. `git reset --hard <tip>` on `/opt/sectorwars-dev`.
3. **Assert before migrating:** 113 migration files present; `alembic current` now *resolves* and
   reports `a7c4e91b2d08`; `alembic heads` is single-head `d7e4a1b9c2f0`.
4. `alembic upgrade head` (3 revisions).
5. Restart `sectorwars-gameserver` (+ frontends, which also pick up 115 commits).
6. **Post-verify by asserted outcome, never exit code** — `alembic upgrade` exiting 0 proves only
   that the runner did not crash, and `alembic stamp` also exits 0 while creating nothing:
   - `alembic_version == d7e4a1b9c2f0`
   - both K2 columns present, **nullable**, `timestamptz` + `integer`
   - all 6 pre-existing player rows still `NULL/NULL` (no backfill declared — also what stops the
     cooldown granting retroactive immunity)
   - `admin.system.health_view` active grants == **1**
   - ORM can SELECT both new attributes
   - gameserver healthy; admin dashboard health panel returns 200 rather than 403

## The real risk, stated plainly

**This is not "apply 3 migrations." It is "bring a host 115 commits current in one step."** Gameserver,
admin-ui and player-client all jump at once. Something unrelated to RBAC can break, and the blast
radius is the whole dev stack — not the three revisions anyone is thinking about.

Mitigating: it is a dev host, Max is the only tester (`admins = 1`, `players = 6`), host content is
fully recoverable, and rollback is `git reset --hard f8e46b1e` plus the migrations' own `downgrade()`.

## Decisions needed from Max

1. **Go / no-go** on a 115-commit fast-forward in one step, versus staging it.
2. **The 45 dirty files** — proposal is *discard*, justified by the content-neutrality result above,
   not by convenience. Say so explicitly if you want them preserved anyway.
3. Whether the frontends restart in the same window or separately.

## Spun-off tickets

- **Rule 6's ceiling did not self-enforce** — the 10-commit/48h auto-ticket never fired. The rule has
  a reader and no writer, the same defect as the amendment tracker.
- **`ServerSetup/docs/services/sectorwars-hosting.md` names interstitch** (offline 29 days) as the dev
  host. `CLAUDE.md` sends every seat to that doc before touching live infrastructure.
- **Audit past live-proof claims against heimdall** — anything "proven live" recently was proven
  against code 115 commits stale.
