import React from 'react';
import RankDisplay from '../ranking/RankDisplay';
import RankProgress from '../ranking/RankProgress';
import MedalShowcase from '../ranking/MedalShowcase';

/**
 * ServiceRecordTab — the StatusBar dossier dropdown's "Service Record" tab
 * (WO-UI0-STATUSBAR sub-part a, Accept #5).
 *
 * There IS already a shipped "SERVICE RECORD console" — pages/RankingPage.tsx
 * (its own doc-comment literally says so) — composing RankDisplay +
 * RankProgress + MedalShowcase + Leaderboard inside a full-page
 * CockpitInstrument frame reached via the rail (RouteRail 'SET'-style nav).
 * Embedding that whole page verbatim here doesn't fit: CockpitInstrument is
 * sized/chromed for a full monitor, and Leaderboard is GALACTIC standings
 * (all players) — a different concern from one player's own service record.
 * So this reuses the SAME three personal-standing views RankingPage does
 * (RankDisplay/RankProgress/MedalShowcase — all zero-prop, self-fetching
 * React.FCs with their own loading/error cycles), deliberately dropping the
 * CockpitInstrument chrome and the Leaderboard, to fit the fixed-size
 * dropdown. Flagged in the WO-UI0-STATUSBAR(a) report for review.
 */
const ServiceRecordTab: React.FC = () => (
  <div className="sb-service-record">
    <RankDisplay />
    <RankProgress />
    <MedalShowcase />
  </div>
);

export default ServiceRecordTab;
