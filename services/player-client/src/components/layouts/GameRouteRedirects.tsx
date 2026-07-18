import React, { useEffect } from 'react';
import { Navigate } from 'react-router-dom';
import { requestTacticalPage } from '../../services/deckNavBus';

/**
 * GameRouteRedirects — WO-UI5-RETIREMENT+GLASS: the 10 legacy /game/* URLs
 * RouteRail's nav keys used to point at (map/player/settings/planets/
 * combat/team/governance/ranking/ships/trading), retired into client-side
 * redirects onto their shipped homes on the single-page cockpit now that
 * RouteRail itself is gone. Every legacy path is nested under the SAME
 * `<Route path="/game" element={<ProtectedRoute><GameShellRoute /></...}>`
 * as its `/game` destination (App.tsx) — a redirect only changes which
 * page/panel is visible inside the already-authenticated shell, never the
 * auth requirement.
 *
 * Deep-linking: of the ten targets, only TACTICAL[TARGET] (the /game/combat
 * home) has an existing cross-chrome navigation channel to hook —
 * services/deckNavBus.ts's `requestTacticalPage`, the same latched pub/sub
 * TacticalMonitor's own [TARGET · THREAT] softkey already consumes for the
 * annunciator lamp click-through (WO-UI1-CHROME-COMPLETE item 6). The other
 * nine targets (StatusBar's dossier tabs, its [⚙] settings popup, the
 * LOCATION-chip governance panel, the NAV monitor's CHART page) are one
 * click away from a bare `/game` landing but have NO equivalent bus today —
 * wiring one would mean adding a consumer inside StatusBar.tsx /
 * LocationDropdown.tsx / GameDashboard.tsx, each owned by a different WO
 * lane and explicitly out of this WO's file scope (glass-lane / rep-lane).
 * Flagged in the STATUS report, not built here — see that report for the
 * full per-route target map.
 */

/** Bare redirect — lands on the always-mounted cockpit shell; the target
 * panel/tab is one click away via its own trigger (the dossier name-chip,
 * [⚙], or the LOCATION chip), all rendered unconditionally by StatusBar on
 * every /game/* route (GameShellRoute -> GameLayout). Also correct,
 * unassisted, for /game/trading: GameDashboard already renders the docked
 * trade desk (stationTerminal defaults to 'trade') whenever is_docked is
 * true and the normal flight view otherwise — no redirect-side deep-link
 * needed for that one either. */
export const RedirectToGame: React.FC = () => <Navigate to="/game" replace />;

/** /game/combat's home: TACTICAL[TARGET] — deep-linked via the existing
 * deckNavBus latch. Fire-and-forget, mirroring every other caller of this
 * bus: the request latches even if TacticalMonitor isn't mounted yet
 * (docked/landed — it only renders in flight mode), and is consumed
 * whenever it next mounts. */
export const RedirectToTacticalTarget: React.FC = () => {
  useEffect(() => {
    requestTacticalPage('target');
  }, []);
  return <Navigate to="/game" replace />;
};
