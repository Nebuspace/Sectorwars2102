# Dependabot Triage — 2026-05-31

Repository: `Nebuspace/Sectorwars2102` (private, default branch `master`)
Data source: `gh api /repos/Nebuspace/Sectorwars2102/dependabot/alerts?state=open` (paginated)
Open alerts at time of pull: **65** (the in-banner "58" appears to be slightly stale)

## 1. Summary

GitHub reports **65 open Dependabot alerts** across the repo: **1 critical, 20 high, 40 moderate, 4 low**. Two ecosystems are affected: **npm (58 alerts)** and **pip (7 alerts)**. No alerts come from Docker / GitHub Actions / other ecosystems.

The picture is dominated by one package: **`axios` accounts for 38 of the 65 alerts (58%)**, all chains derived from the well-publicized Nov 2025 / 2026 axios prototype-pollution / NO_PROXY / proxy-bypass disclosures. The repo has axios at `1.13.6` in two lockfiles (root `package-lock.json` and `services/player-client/package-lock.json`); the `package.json` constraints already say `^1.15.1` / `^1.15.2`, but the lockfiles were never refreshed. A single `npm install` would push axios to `1.16.x` and clear all 38 axios alerts at once.

**There are zero open or merged Dependabot PRs** (`gh pr list --author "app/dependabot"` returns empty). **There is no `.github/dependabot.yml` config**, so Dependabot is only producing alerts, not auto-PRs. That explains the backlog: nothing is being proposed automatically, and no human has bumped lockfiles since the affected packages shipped fixes.

After clearing axios, the remaining 27 alerts cluster on a handful of packages: `vite` (6, both lockfiles), `dompurify` (4, player-client), `i18next-http-backend` (2), `follow-redirects` (2), `picomatch` (2), `starlette` (2, gameserver), `black` (2, gameserver), plus singles for `h11`, `ecdsa`, `semver`, `uuid`, `postcss`, `brace-expansion`, `pytest`.

## 2. Critical findings

| CVE / GHSA | Package | Severity | Affected range | Patched in | Where in repo | Notes / URL |
|---|---|---|---|---|---|---|
| CVE-2025-43859 / GHSA-vqfr-h8mv-ghfj | `h11` (pip) | **Critical** (CVSS 9.1) | `< 0.16.0` | `0.16.0` | `services/gameserver/poetry.lock` (currently `0.14.0`) | h11 accepts malformed Chunked-Encoding bodies → request smuggling risk on the FastAPI/uvicorn server. h11 is a transitive dep (via httpx / uvicorn). Bump is patch-level. [alert #9](https://github.com/Nebuspace/Sectorwars2102/security/dependabot/9) |

### CVSS 8.0+ HIGH alerts worth promoting visually

| CVE / GHSA | Package | CVSS | Patched in | Where | URL |
|---|---|---|---|---|---|
| CVE-2026-44494 / GHSA-35jp-ww65-95wh | `axios` | 8.7 | `1.16.0` | both `package-lock.json` files | [#162](https://github.com/Nebuspace/Sectorwars2102/security/dependabot/162), [#156](https://github.com/Nebuspace/Sectorwars2102/security/dependabot/156) |
| CVE-2026-44492 / GHSA-pjwm-pj3p-43mv | `axios` | 8.6 | `1.16.0` | both `package-lock.json` files | [#161](https://github.com/Nebuspace/Sectorwars2102/security/dependabot/161), [#155](https://github.com/Nebuspace/Sectorwars2102/security/dependabot/155) |

All other "high" entries are CVSS ≤ 7.5 and are covered by the axios / vite roll-up in §7.

## 3. High findings — grouped by package

### `axios` (npm) — 14 high alerts
- **Constraint in manifests:** `^1.15.1` (root `package.json`), `^1.15.2` (player-client)
- **Installed in lockfiles:** `1.13.6` in **both** `package-lock.json` and `services/player-client/package-lock.json` (lockfile is stale vs. manifest)
- **Distinct CVEs blocked here (high tier):** CVE-2026-44492, CVE-2026-44494, CVE-2026-44495, CVE-2026-42043, CVE-2026-42264, CVE-2026-42033, CVE-2026-42035 (each appears twice — once per lockfile)
- **Minimum bump to clear ALL high+medium+low axios alerts:** `axios >= 1.16.0`
- **Semver risk:** minor bump from 1.13 → 1.16. No breaking-change notes in axios 1.x. Already permitted by the existing caret ranges — would be applied by `npm install` alone.

### `vite` (npm) — 2 high alerts
- **player-client `package.json`:** `^8.0.5`, **lockfile installed:** `8.0.0` (lockfile stale)
- **admin-ui `package.json`:** `^4.4.9`, **lockfile installed:** `4.5.14` (still vulnerable; needs major bump)
- **High CVEs:** CVE-2026-39364 (`server.fs.deny` bypass), CVE-2026-39363 (arbitrary file read via dev-server WebSocket)
- **Bump needed:**
  - player-client: `8.0.5` (patch; already in `^8.0.5` range — only `npm install` needed)
  - admin-ui: from `^4.4.9` → `^5.4.20` or `^6.4.2` (major bump). Also pulls in a `@vitejs/plugin-react` bump.
- **Semver risk:** admin-ui jump is a **major** version change (4 → 5 or 4 → 6). Vite 5 dropped Node 14/16 support and tightened ESM defaults; Vite 6 changed environment API. Needs a manual smoke-test on admin-ui dev/build.

### `starlette` (pip) — 1 high alert
- **Constraint:** transitively pulled by `fastapi 0.103.2`. Lockfile pins `starlette 0.27.0`
- **CVE:** CVE-2024-47874 (multipart/form-data DoS, GHSA-f96h-pmfr-66vw). Patched in `0.40.0`
- **Bump needed:** `starlette >= 0.40.0`. Cannot be done directly because `fastapi==0.103.2` requires `starlette>=0.27.0,<0.28.0`. **Requires upgrading FastAPI** (probably to `0.115.x` or newer) so that the modern starlette is accepted.
- **Semver risk:** FastAPI 0.103 → 0.115 is technically a minor in their scheme but historically has small breaking changes (lifespan, Annotated dependency forms, Pydantic v2 details). Needs an API audit.

### `black` (pip) — 1 high alert
- **Lockfile:** `black 23.12.1`. Dev-only dependency (`tool.poetry.group.dev.dependencies`)
- **CVE:** CVE-2026-32274 (arbitrary file writes from unsanitized user input in cache filename). Patched in `26.3.1`.
- **Bump:** `^23.7.0` → `^26.3.1` — **major bump**, but dev-only formatter. Practical risk: black 24/25/26 default style is unchanged outside minor formatting tweaks. Low real-world risk.

### `ecdsa` (pip) — 1 high alert
- **Lockfile:** `ecdsa 0.19.2`. Pulled via `python-jose[cryptography]`.
- **CVE:** CVE-2024-23342 (Minerva timing attack on P-256). **`first_patched_version` is null — no fix is available**.
- **Bump:** N/A. Upstream (`python-jose`) is essentially unmaintained. Real mitigation is to migrate JWT signing/verification to `authlib` or `pyjwt[crypto]`, which use `cryptography` instead of `python-ecdsa`. This is a code change, not a dep bump.
- **Semver risk:** **breaking change**, library swap. Needs Max input.

### `semver` (npm) — 1 high alert
- **Lockfile (root):** `semver 4.3.2` (very old, transitive via legacy tooling)
- **CVE:** CVE-2022-25883 (ReDoS). Patched in `5.7.2` / `6.3.1` / `7.5.2`.
- **Bump:** trivial; whichever dep is pulling in `semver@4` should resolve to `^5.7.2` after a lockfile refresh. May require finding and updating the parent. Likely cleared by a generic `npm audit fix` or a full `npm install` after dependency updates.

## 4. Moderate + Low — counts only

### Moderate (40 total)

| Package | Ecosystem | Severity | CVE count | Patched in | Where |
|---|---|---|---|---|---|
| `axios` | npm | medium | 22 | `1.16.0` (clears all) | both lockfiles |
| `dompurify` | npm | medium | 4 | `3.4.0` | player-client lockfile (transitive via `isomorphic-dompurify`) |
| `i18next-http-backend` | npm | medium | 2 | `3.0.5` | admin-ui + player-client |
| `vite` | npm | medium | 2 | `6.4.2` / `8.0.5` | admin-ui + player-client |
| `picomatch` | npm | medium | 2 | `2.3.2` / `4.0.4` | player-client (transitive) |
| `follow-redirects` | npm | medium | 2 | `1.16.0` | both lockfiles (transitive via old axios) |
| `starlette` | pip | medium | 1 | `0.47.2` | gameserver |
| `black` | pip | medium | 1 | `24.3.0` | gameserver (dev) |
| `pytest` | pip | medium | 1 | `9.0.3` | gameserver (dev) |
| `uuid` | npm | medium | 1 | `11.1.1` | root lockfile |
| `postcss` | npm | medium | 1 | `8.5.10` | player-client (transitive) |
| `brace-expansion` | npm | medium | 1 | `1.1.13` | root lockfile |

### Low (4 total)

| Package | Ecosystem | Severity | CVE count | Patched in | Where |
|---|---|---|---|---|---|
| `axios` | npm | low | 2 | `1.15.1` (cleared by 1.16.0 bump) | both lockfiles |
| `vite` | npm | low | 2 | `5.4.20` | admin-ui (CVE-2025-58751, CVE-2025-58752) |

## 5. Existing Dependabot PRs

`gh pr list --repo Nebuspace/Sectorwars2102 --author "app/dependabot" --state all --limit 50` → **empty list**.

No Dependabot PRs have ever been opened (or all closed without trace). This is consistent with §6 — no `.github/dependabot.yml` exists, so the `version-updates` reconciler that opens PRs is not enabled. Only the `security advisories → alerts` half is running.

## 6. Dependabot config audit

- **`.github/dependabot.yml`: does not exist.**
- Consequence: Dependabot opens **zero PRs**. The 65 alerts will sit indefinitely until manually addressed; new releases of axios / vite / fastapi will not produce automated PRs either.
- **Recommendation (post-triage, not part of this doc's scope):** add a minimal config covering `npm` in three roots (`/`, `/services/player-client`, `/services/admin-ui`) and `pip` in `/services/gameserver`, weekly schedule, grouped security updates. Suggest also enabling auto-merge for patch-level updates on dev dependencies.

## 7. Recommended action plan

Ranked. Effort/risk labels: **no-brainer** = lockfile refresh only; **needs API audit** = code/test verification needed; **breaking** = manual code changes / library swap.

| # | Action | CVEs cleared | Severity addressed | Effort/risk | Delivery |
|---|---|---|---|---|---|
| 1 | **Refresh axios in both `package-lock.json` files to `1.16.0+`.** `package.json` constraints (`^1.15.1`, `^1.15.2`) already allow this — run `npm install` in repo root and in `services/player-client/`. | 38 (14 high + 22 med + 2 low) | 14 high + 22 medium + 2 low | no-brainer | Single PR, no manifest changes (lock-only). Auto-merge candidate. |
| 2 | **Bump `h11` to `0.16.0+` in `services/gameserver/poetry.lock`.** Run `poetry update h11`; should be a transitive constraint update only. | 1 (CVE-2025-43859) | 1 **critical** | no-brainer (verify uvicorn/httpx still resolve) | Same PR as #1 or sibling. |
| 3 | **Bump player-client `vite` to `8.0.5+`** (already in `^8.0.5` range; just `npm install`). | 3 (2 high + 1 med) on player-client side | 2 high + 1 medium | no-brainer | Folds into PR #1. |
| 4 | **Refresh transitive npm deps in lockfiles:** `dompurify ≥ 3.4.0`, `i18next-http-backend ≥ 3.0.5` (note: 3.x is a major from current 2.7.3 — see §8), `picomatch ≥ 2.3.2` (or `4.0.4`), `follow-redirects ≥ 1.16.0`, `postcss ≥ 8.5.10`, `brace-expansion ≥ 1.1.13`, `uuid ≥ 11.1.1`, `semver ≥ 5.7.2`. Mostly cleared by `npm install` after #1, with `npm audit fix` for stragglers. | 16 alerts | 1 high (semver) + 15 medium | no-brainer for transitive; `i18next-http-backend` 2→3 is a **major** bump and direct dep | Folds into PR #1 OR split out `i18next-http-backend` if it needs a code change. |
| 5 | **Bump admin-ui `vite` from 4 → 5 or 6.** Requires `package.json` change + `@vitejs/plugin-react` co-bump. | 3 (1 med + 2 low) | 1 medium + 2 low | needs API audit (Vite major) — verify admin-ui dev server + build | Separate PR, manual smoke test. |
| 6 | **Bump `black` to `^26.3.1`** in `services/gameserver/pyproject.toml`. Dev-only. | 2 (1 high + 1 med) | 1 high + 1 medium | needs API audit (style stability check; CI lint job will catch any reflow) | Separate small PR. |
| 7 | **Upgrade FastAPI to enable `starlette ≥ 0.47.2`.** Today `fastapi==0.103.2` pins `starlette<0.28`. Likely target: `fastapi ≥ 0.115`. | 2 (1 high + 1 med) | 1 high + 1 medium | needs API audit — FastAPI route/dependency shape, Pydantic v2 compatibility | Separate PR with gameserver test pass. |
| 8 | **Bump `pytest` to `^9.0.3`** in gameserver dev deps. | 1 (CVE-2025-71176) | 1 medium | needs API audit (pytest 7 → 9 is two majors; check fixtures + collection) | Separate small PR. |
| 9 | **Replace `python-ecdsa` usage** by migrating away from `python-jose` to `pyjwt` or `authlib`. | 1 (CVE-2024-23342, no upstream fix) | 1 high | **breaking** — code change in JWT layer | Defer; needs Max sign-off. |
| 10 | **Add `.github/dependabot.yml`** with weekly schedule, three npm roots + one pip root, grouped security updates. | n/a | preventative | no-brainer | Separate small PR. |

Roll-up suggestion: **PR A** = items 1+2+3+4 (the npm lockfile refresh + h11 patch) — covers ~57 of 65 alerts in one no-risk PR. **PR B** = item 6 (black). **PR C** = item 5 (admin-ui vite major). **PR D** = item 7 (fastapi/starlette). **PR E** = item 10 (Dependabot config). Items 8 and 9 can wait for Max input.

## 8. Open questions for Max

1. **`i18next-http-backend` 2 → 3:** the patched version (`3.0.5`) is a **major** bump from your current `^2.7.3`. The v3 changelog tightened the default fetch behavior and renamed a few options. Acceptable to bump now, or do you want it deferred behind a translation-loading smoke test?
2. **admin-ui Vite major (4 → 5 or 6):** which target? Vite 5 is the minimum to clear CVEs; Vite 6 is the current stable. Vite 6 changes the Environment API in ways that can affect custom plugins — easier to bump to 5 first if low-risk is the priority.
3. **FastAPI bump for starlette CVEs:** `fastapi 0.103.2` is from late 2023. Are you intentionally pinned, or open to bumping to `0.115.x`? Bumping is needed to clear two starlette CVEs (1 high, 1 medium) and gets you Pydantic-v2-native FastAPI improvements.
4. **`python-ecdsa` Minerva timing attack (no fix available):** do you want to invest in migrating the JWT signing layer off `python-jose`, or accept this as a known issue and dismiss-with-reason in the Dependabot UI? The attack requires a privileged network position to be practical.
5. **`pytest` 7 → 9 (dev-only):** dev tooling, no production impact. OK to bump now, or wait until you have time to fix any fixture/collection drift?
6. **Intentional pins to *leave alone*:** anything in `gameserver/pyproject.toml` or the npm manifests that you've pinned on purpose (e.g., `fastapi==0.103.2` is suspiciously exact — is that intentional)?
7. **Enable Dependabot version-updates PRs going forward?** Recommend yes; want it gated by reviewer (default) or set up with auto-merge for patch-level dev-dep bumps?

---

### Appendix A — full alert inventory (count check)

- Total open: 65 (Dependabot counts what GitHub UI calls "open alerts"; the banner number "58" may be cached / pre-recent disclosures)
- Severity breakdown: 1 critical · 20 high · 40 medium · 4 low
- Ecosystem breakdown: 58 npm · 7 pip · 0 docker / actions / other
- Package count: 15 distinct packages
- Most-impacted package: `axios` (38 alerts, 58% of total)
- Manifests with alerts:
  - `package-lock.json` (repo root, the e2e/dev harness)
  - `services/player-client/package-lock.json`
  - `services/admin-ui/package.json` (Dependabot reports against manifest, not lock, here)
  - `services/gameserver/poetry.lock`
- No alerts in: `services/admin-ui/package-lock.json` (Dependabot tracks manifest), `services/region-manager`, `services/nginx-gateway`, `services/database`, root pip/poetry, GitHub Actions workflows.
