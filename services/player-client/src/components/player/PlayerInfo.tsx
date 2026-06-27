import React, { useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import GameLayout from '../layouts/GameLayout';
import { EmbeddedContext } from '../cockpit/EmbeddedContext';
import { ShipSelector } from '../ships/ShipSelector';
import { PlanetManager } from '../planetary/PlanetManager';
import ReputationPage from '../mfd/pages/ReputationPage';
import { formatCredits } from '../../utils/formatters';
import './player-info.css';

/**
 * PlayerInfo — the consolidated player view (WO-PLAYERINFO id=144). ONE place
 * for Identity · Reputation · Fleet/Hangar · Colony Roster, tabbed so the
 * primary section is visible at 1440×900 (SCROLL LAW: the active section scrolls
 * internally, the page never scrolls). Reuses the existing views rather than
 * duplicating logic — the shell-wrapped ones (ShipSelector/PlanetManager) render
 * bare here via EmbeddedContext so two cockpit shells never nest; ReputationPage
 * is an MFD-page body and drops in directly. The rail button (HGR→PLY) is wired
 * separately in id=147; this view stands alone at /game/player.
 */
type PiTab = 'identity' | 'reputation' | 'hangar' | 'colonies';

const TABS: Array<{ id: PiTab; label: string }> = [
  { id: 'identity', label: 'IDENTITY' },
  { id: 'reputation', label: 'REPUTATION' },
  { id: 'hangar', label: 'HANGAR' },
  { id: 'colonies', label: 'COLONIES' },
];

const IdentitySection: React.FC = () => {
  const { user } = useAuth();
  const { playerState } = useGame();
  return (
    <div className="pi-identity">
      <div
        className="pi-id-name"
        style={{ color: playerState?.name_color || '#00D9FF' }}
        title={user?.username}
      >
        {user?.username || '—'}
      </div>
      <div className="pi-id-grid">
        <div className="pi-id-field">
          <span className="pi-id-k">RANK</span>
          <span className="pi-id-v">{playerState?.military_rank || '—'}</span>
        </div>
        <div className="pi-id-field">
          <span className="pi-id-k">REPUTATION</span>
          <span className="pi-id-v">{playerState?.reputation_tier || '—'}</span>
        </div>
        <div className="pi-id-field">
          <span className="pi-id-k">CREDITS</span>
          <span className="pi-id-v">{formatCredits(playerState?.credits)}</span>
        </div>
        <div className="pi-id-field">
          <span className="pi-id-k">TURNS</span>
          <span className="pi-id-v">
            {playerState?.turns?.toLocaleString() ?? '0'}
            {typeof playerState?.max_turns === 'number'
              ? ` / ${playerState.max_turns.toLocaleString()}`
              : ''}
          </span>
        </div>
      </div>
    </div>
  );
};

const PlayerInfo: React.FC = () => {
  const [tab, setTab] = useState<PiTab>('identity');
  return (
    <GameLayout>
      <div className="player-info">
        <div className="pi-tabs" role="tablist" aria-label="Player info sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`pi-tab${tab === t.id ? ' active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="pi-body">
          {tab === 'identity' && <IdentitySection />}
          {tab === 'reputation' && <ReputationPage />}
          {tab === 'hangar' && (
            <EmbeddedContext.Provider value={true}>
              <ShipSelector />
            </EmbeddedContext.Provider>
          )}
          {tab === 'colonies' && (
            <EmbeddedContext.Provider value={true}>
              <PlanetManager />
            </EmbeddedContext.Provider>
          )}
        </div>
      </div>
    </GameLayout>
  );
};

export default PlayerInfo;
