# monk — memory · project: Sectorwars2102

## What I've learned about this project

- Build: `npm run build` (Vite) · Types: `npx tsc --noEmit` · Tests: `npx vitest run <glob>`
- Test harness is jsdom + `react-dom/client` createRoot + act() — NO @testing-library/react. Mirrors StatusBar.smoke.test.tsx pattern.
- `cockpit-shell.css` is the RATIFIED artifact baseline (do not modify) — defines `.mon`/`.mhead`/`.mbody`/`.skrow`/`.skey` etc.
- `DeckPageTabs` is already imported in `GameDashboard.tsx` (line 26). No import change needed to use it.
- Deck monitors use: `.mon <name>-monitor > .mhead(.mtitle + buttons) + .mbody(role=tabpanel) + .skrow(DeckPageTabs)`.
- `DeckPageTabs` renders null for < 2 pages. SpaceDock (1 venue) → no DeckPageTabs, no skrow.
- The `station-monitor` migration required replicating trading-interface.css compaction rules under `.station-monitor .station-venue-body` in cockpit.css (trading-interface.css is out of scope to touch).
- `.mbody` brings `font-size:.62em; color:#8CA2BA; padding:.6em .8em` — override these for venue contexts that have their own comprehensive CSS (`font-size:1em; color:inherit; padding:0` on `.station-monitor .mbody`).
- `git diff --name-only` to confirm scope: only owned player-client paths.

## How I operate here (my working notes)

- For CSS class migrations: grep all consumers first, then check CSS files for dead vs live selectors.
- Always check test files in `__tests__/` that might assert on old class names before removing them.
- The `.screen-hud-content` class adds margins + side borders (old CRT aesthetic) — do NOT carry it forward into `.mon` anatomy contexts.

## Gotchas — things that bit me (don't repeat)

- Adding `screen-hud-content` as an extra class on a `mbody` element seems safe but actually cascades the old CRT borders (border-left/border-bottom) and margin into the new anatomy — avoid it.
- `trading-interface.css` scopes compaction rules under `.screen-hud-content` parent — when removing that class from the venue wrapper, replicate those rules under the new selector in cockpit.css.
