import React from 'react';
import RankDisplay from '../ranking/RankDisplay';
import RankProgress from '../ranking/RankProgress';
import MedalShowcase from '../ranking/MedalShowcase';
import { CombatHistoryPanel } from '../combat/CombatHistoryPanel';
import BountyBoard from '../ranking/BountyBoard';

/**
 * ServiceRecordTab — the StatusBar dossier dropdown's "Service Record" tab
 * (WO-UI0-STATUSBAR sub-part a, Accept #5).
 *
 * There IS already a shipped "SERVICE RECORD console" — pages/RankingPage.tsx
 * (its own doc-comment literally says so) — composing RankDisplay +
 * RankProgress + MedalShowcase + Leaderboard inside a full-page
 * CockpitInstrument frame (formerly reached via RouteRail's 'SET'-style nav;
 * RouteRail itself is retired, WO-UI5-RETIREMENT+GLASS, and /game/ranking
 * is now a client-side redirect onto this dossier tab, GameRouteRedirects.tsx).
 * Embedding that whole page verbatim here doesn't fit: CockpitInstrument is
 * sized/chromed for a full monitor, and Leaderboard is GALACTIC standings
 * (all players) — a different concern from one player's own service record.
 * So this reuses the SAME three personal-standing views RankingPage does
 * (RankDisplay/RankProgress/MedalShowcase — all zero-prop, self-fetching
 * React.FCs with their own loading/error cycles), deliberately dropping the
 * CockpitInstrument chrome and the Leaderboard, to fit the fixed-size
 * dropdown. Flagged in the WO-UI0-STATUSBAR(a) report for review.
 *
 * LEG-372: CombatHistoryPanel mounts here so browse is reachable — the
 * legacy /game/combat Weapons Console route was retired to TACTICAL[TARGET]
 * and no longer mounts CombatInterface.
 * LEG-156: public Federation BountyBoard (available bounties) under the
 * personal medals — browse surface, not fabricated portraits/kill logs.
 */
const ServiceRecordTab: React.FC = () => (
  <div className="sb-service-record">
    <RankDisplay />
    <RankProgress />
    <MedalShowcase />
    <CombatHistoryPanel />
    <BountyBoard />
  </div>
);

export default ServiceRecordTab;
