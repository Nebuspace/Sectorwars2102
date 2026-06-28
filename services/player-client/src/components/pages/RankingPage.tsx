import React from 'react';
import { useGame } from '../../contexts/GameContext';
import GameLayout from '../layouts/GameLayout';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import RankDisplay from '../ranking/RankDisplay';
import RankProgress from '../ranking/RankProgress';
import MedalShowcase from '../ranking/MedalShowcase';
import Leaderboard from '../ranking/Leaderboard';
import './ranking-page.css';

/**
 * RankingPage — the SERVICE RECORD console.
 *
 * Read-only view over the military ranking system: current rank and
 * bonuses, progress toward the next rank, earned/available medals,
 * and the public leaderboard. All data comes from /api/v1/ranking/*.
 */
const RankingPage: React.FC = () => {
  const { playerState } = useGame();

  return (
    <GameLayout>
      <CockpitInstrument
        title="SERVICE RECORD"
        accent="#FFD700"
        subtitle={'MILITARY RANKING & COMMENDATIONS'}
      >
      <div className="ranking-page">
        {/* Old page-level header removed — the instrument LED header
            carries the title and subtitle (Law 3). */}
        <div className="ranking-page-grid">
          <section className="ranking-column ranking-column-left">
            <div className="ranking-panel hud-panel">
              <div className="ranking-panel-header">
                <h3 className="ranking-panel-title">CURRENT RANK</h3>
              </div>
              <RankDisplay />
            </div>

            <div className="ranking-panel hud-panel">
              <div className="ranking-panel-header">
                <h3 className="ranking-panel-title">PROMOTION TRACK</h3>
              </div>
              <RankProgress />
            </div>

            <div className="ranking-panel hud-panel">
              <div className="ranking-panel-header">
                <h3 className="ranking-panel-title">COMMENDATIONS</h3>
              </div>
              <MedalShowcase />
            </div>
          </section>

          <section className="ranking-column ranking-column-right">
            <div className="ranking-panel hud-panel">
              <div className="ranking-panel-header">
                <h3 className="ranking-panel-title">GALACTIC STANDINGS</h3>
              </div>
              <Leaderboard playerId={playerState?.id ?? null} />
            </div>
          </section>
        </div>
      </div>
      </CockpitInstrument>
    </GameLayout>
  );
};

export default RankingPage;
