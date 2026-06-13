/**
 * NAV / POSITION — MFD ops page (NEON15 B3).
 *
 * Position fix from GameContext.currentSector plus the charted exits from
 * availableMoves ({ warps, tunnels } of MoveOption). Rows are display-only —
 * no click-to-warp in v1; the helm owns movement.
 */

import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import type { MoveOption } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty, MFDInsufficient } from '../atoms';
import './pages-ops.css';

const ACCENT = '#00D9FF';

/** One charted exit row — warp or tunnel, tagged accordingly. */
const ExitRow: React.FC<{ move: MoveOption; tunnel: boolean }> = ({ move, tunnel }) => (
  <li className="mfd-page-nav-exit">
    <span className="mfd-page-nav-exit-num">{move.sector_number ?? move.sector_id}</span>
    <span className="mfd-page-nav-exit-name">{move.name || '—'}</span>
    {tunnel && <span className="mfd-page-nav-exit-tag">TUN</span>}
    <span className={`mfd-page-nav-exit-cost${move.can_afford ? '' : ' over'}`}>
      {move.turn_cost}T
    </span>
  </li>
);

const NavPositionPage: React.FC = () => {
  const { currentSector, availableMoves } = useGame();

  const { warps, tunnels } = availableMoves;

  return (
    <div className="mfd-page-ops">
      <MFDPageHeader title="NAV / POSITION" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="nav-position">
        {!currentSector ? (
          <MFDInsufficient text="NO POSITION FIX" />
        ) : (
          <>
            <MFDField
              label="SECTOR"
              value={currentSector.sector_number ?? currentSector.sector_id}
              accent
            />
            <MFDField label="NAME" value={currentSector.name || '—'} />
            <MFDField label="TYPE" value={currentSector.type || '—'} />
            <MFDField label="HAZARD" value={currentSector.hazard_level ?? '—'} />
            {currentSector.region_name ? (
              <MFDField label="REGION" value={currentSector.region_name} />
            ) : null}
          </>
        )}

        <div className="mfd-page-section-label">CHARTED EXITS</div>
        {warps.length === 0 && tunnels.length === 0 ? (
          <MFDEmpty text="NO CHARTED EXITS" />
        ) : (
          <ul className="mfd-page-nav-exits">
            {warps.map((move) => (
              <ExitRow key={`w-${move.sector_id}`} move={move} tunnel={false} />
            ))}
            {tunnels.map((move) => (
              <ExitRow key={`t-${move.sector_id}`} move={move} tunnel={true} />
            ))}
          </ul>
        )}
      </MFDPageBody>
    </div>
  );
};

export default React.memo(NavPositionPage);
