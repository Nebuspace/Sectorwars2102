# WAVE-2.5 WO-UI0-SHELL-TRANSPLANT — build plan (Branch A ruled 2026-07-13; guardrail retired)

Target: transplant the v10 artifact `.stage` shell (single-column grid rows **sbar/band/tele/lower**; windshield → contained 15.5em band; `.lower` = 19% `.mfdcol`[2 MFD] + 81% `.deck`[3 mon]). Artifact = `audit/design-briefs/cockpit-redesign-v10-RATIFIED.html` (L436-484 markup, L29-420 CSS). Contract CSS = `cockpit-shell.css` (css-lift DONE, inert).

## Mechanism (Rook-adjudicated): (a) PORTALS — forced + minimal
grid-area places only DIRECT children (GameLayout:502-508 proves it); GameDashboard mounts 2 levels deep (:485) → scene+deck can't be grid-placed while nested. So:
- GameLayout renders `.stage` grid with always-present slots: `.sbar`(StatusBar) · `.band`(empty+ref) · `.tele`(Teleprinter) · `.lower`→`.mfdcol`(relocated MFD `<aside>` from :425-436) + `.deck`(empty+ref). Provides `{bandEl,deckEl}` via context (element-in-state; guard `bandEl && createPortal(...)`).
- GameDashboard: 2 changes only — `createPortal(.cockpit-windshield, bandEl)` + `createPortal(.cockpit-console, deckEl)`; residual (alerts :2098-2277, modals) stays at an overlay host. Keeps the 3665-line state tree intact.

## SEQUENCE (hard):
1. **Rail kernel (task 7, IN FLIGHT)** — MFDSoftkeyRail+DeckPageTabs→one component. MUST land+commit FIRST (it edits GameDashboard monitor consumers; the seam also edits GameDashboard → serialize, never concurrent).
2. **SEAM worker (serial, alone on GameDashboard/GameLayout/CSS)** — after rail commit.
3. **5 LEAVES (parallel, after seam merges green)** — L2/L3 sequence AFTER the rail (they consume the unified rail).
4. Integrate → Pixel → Playwright → commit → HEADS-UP → orchestrator visual-diff.

## SEAM worker owns (unparallelizable — grid-topology touches all):
- `GameLayout.tsx` — `.stage` grid; slot divs + slot-element context; MFD-col relocate into `.lower`; **Annunciator re-mount INTO `.band`** (retire `.windshield-hud-anchor`); **Teleprinter row move to row3**; kill obsolete `--teleprinter-h` useLayoutEffect (:286-302).
- `GameDashboard.tsx` — 2 createPortal wraps + residual host.
- `game-layout.css` — `.stage` grid; RETIRE the 3-var absolute geometry (:113-118,182,191,204-211).
- `cockpit.css` (:943-997) — strip `.cockpit-console` position:absolute + shell-var deps; deck → grid child of `.lower`.

## LEAVES (parallel after seam):
- L1 StatusBar `.sbar`/`.chip`/`.vit` + classname re-emit → statusbar.css (real vitals NO shield; REP `<Tier> +N`; ⏻ compact)
- L2 MFD frame `.mfd`/`.scr`/`.skrow` → mfd.css (bezel+CRT scanlines; 5-slot middot rails; POS rename) — AFTER rail
- L3 Monitor `.mon`/`.mhead`/`.mbody` + softkeys→bottom `.skrow` → monitor CSS — AFTER rail
- L4 Teleprinter `.tele` skin → teleprinter.css (grid-area seam-owned; phosphor-green n4 free)
- L5 Annunciator `.annun`/`.lamp`/`.bulb` BARE-classname re-emit → annunciator.css + Annunciator.tsx (retire prefixed classnames + old CSS; n1 caut≥5; n5 LAW .live-red)

## 4 TRAPS (gate each):
(i) band-height flips: live uses CLASS `.mode-station/.mode-surface` (GameLayout:356) NOT `[data-mode]` → port `.stage[data-mode=x] .band` to `.mode-station .band`. Gate: computed .band height 15.5/8.5/16em.
(ii) scene position:absolute: portaled into `.band`(position:relative) → scene root inset:0. Gate: scene-canvas bbox == .band bbox.
(iii) annunciator anchor: retire `.windshield-hud-anchor`(game-layout.css:182), mount inside `.band`. Gate: `.annun` descendant of `.band`, bbox ⊆ .band, never overlaps `.sbar` (guardrail INTENT survives structurally).
(iv) 🔴 HEADLINE: deck's 3-var dependence (cockpit.css:952-956). Removing --sidebar-w/--band-h/--deck-h → leftover `var(--deck-h)` silently drops rules. Also .console-expand (game-layout.css:191) + the --teleprinter-h effect are obsolete.

## 2 HEADLINE GATES:
- **State round-trip:** /game → MFD-B=COMM + teleprinter=mid-panel + note sector → /game/galaxy → back → assert all preserved. Tripwire: MFDProvider(:401) + teleprinterDisplayMode(:184) stay GameLayout-owned. + portal asserts (`.band` empty after nav-away, no orphan; `querySelectorAll('.band .cockpit-windshield').length===1`).
- **11-file 3-var grep sweep (NON-NEGOTIABLE):** zero `--band-h/--sidebar-w/--deck-h/--statusbar-h/--teleprinter-h` readers in scene/sidebar/deck rules across: GameLayout.tsx, AnnunciatorMini.tsx, statusbar.css, game-layout.css, cockpit.css, SolarSystemViewscreen.tsx, solar-system-viewscreen.css, player-info.css, cockpit-instrument.css, responsive.css.
