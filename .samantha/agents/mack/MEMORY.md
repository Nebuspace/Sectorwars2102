# mack — memory · project: Sectorwars2102 (player-client)

## What I've learned about this project
- Build/test: `cd services/player-client && npx vitest run <file>` (jsdom env via `// @vitest-environment jsdom` header). React 19 + React 18 StrictMode (`main.tsx`). `npx tsc --noEmit` is the real type gate (vite build doesn't type-check).
- Cockpit chrome lives in `components/layouts/GameLayout.tsx` (persistent shell, mounted once by `GameShellRoute` around the `/game/*` Outlet) + `components/pages/GameDashboard.tsx` (the `/game` index route, 3600+ lines).

## How I operate here (my working notes)
- When a finding hinges on React reconciliation behavior (remount vs. update), don't just reason from memory of the spec — write a 20-line isolated vitest probe with a mount/unmount-counting child and get the real number. Cheap, decisive, and this codebase's own conventions (`test-the-discriminating-fact-before-choosing`) reward it. Delete the probe file after (never leave scratch test files in the tree — read-only review).
- `git show HEAD:<path>` vs. the working tree is the fast way to prove a CSS/JS wiring regression is real (not pre-existing dead code) when a WO retires a variable/pattern.

## Gotchas — things that bit me (don't repeat)
- [[portal-fallback-forces-remount]] — `target ? createPortal(node, target) : node` at the same JSX position is NOT a DOM relocation; it's a fiber type flip (HostComponent -> HostPortal) that unmounts+remounts the whole subtree. Empirically confirmed (mountCount 1->2, unmountCount 0->1) with a plain createRoot+act probe, no StrictMode needed — this bites in prod too, not just dev.
- [[css-var-retirement-orphans-js-toggle]] — WO-UI0-SHELL-TRANSPLANT retired `--sidebar-w`/`--band-h`/etc. and hardcoded `.mfdcol` to a fixed CSS %, but left `sidebarOpen`/`toggleSidebar`/the edge-toggle button/the WO-129-B auto-collapse-on-landing effect fully wired in GameLayout.tsx with zero CSS reader left (`.console-collapsed` had exactly one rule, in the pre-seam `game-layout.css`, and it was dropped without a replacement). A CSS-variable retirement sweep needs to grep for every JS-side reader (state, class-toggle, aria-label) of the retired var's OLD class hook, not just the CSS declarations themselves.
