/**
 * REPUTATION — MFD ops page (NEON15 B3).
 *
 * Personal standing straight off GameContext.playerState — the fields
 * verified live: personal_reputation (number), reputation_tier (string),
 * military_rank (string). No bounty/faction fetch in v1.
 */

import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import './pages-ops.css';

const ACCENT = '#FFD700';

const ReputationPage: React.FC = () => {
  const { playerState } = useGame();

  return (
    <div className="mfd-page-ops">
      <MFDPageHeader title="REPUTATION" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="reputation">
        {!playerState ? (
          <MFDInsufficient />
        ) : (
          <>
            <MFDField label="STANDING" value={playerState.personal_reputation ?? '—'} accent />
            <MFDField label="TIER" value={playerState.reputation_tier || '—'} />
            <MFDField label="RANK" value={playerState.military_rank || '—'} />
          </>
        )}
      </MFDPageBody>
    </div>
  );
};

export default React.memo(ReputationPage);
