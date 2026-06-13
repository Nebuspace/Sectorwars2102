/**
 * COMMS / CREW — MFD ops page (NEON15 B3, status: partial).
 *
 * Uplink state + sector presence from WebSocketContext, unread mailbox count
 * and crew affiliation from GameContext. v1 is passive: no team fetch (the
 * affiliation row reports presence only) and no hail composer here — the
 * COMMS station owns messaging.
 *
 * Note: sectorPlayers entries carry username/connected_at only (verified
 * against WebSocketContext) — there is no player "type" field to render.
 */

import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty } from '../atoms';
import './pages-ops.css';

const ACCENT = '#00FF7F';

const CommsCrewPage: React.FC = () => {
  const { playerState, unreadMessageCount } = useGame();
  const { isConnected, sectorPlayers } = useWebSocket();

  return (
    <div className="mfd-page-ops">
      <MFDPageHeader title="COMMS / CREW" accent={ACCENT} status="partial" />
      <MFDPageBody scrollKey="comms-crew">
        <MFDField label="UPLINK" value={isConnected ? 'LINK OK' : 'LINK DOWN'} accent={isConnected} />
        <MFDField label="UNREAD" value={unreadMessageCount ?? '—'} />

        <div className="mfd-page-section-label">PILOTS IN SECTOR</div>
        {sectorPlayers.length === 0 ? (
          <MFDEmpty text="NO CONTACTS IN SECTOR" />
        ) : (
          <ul className="mfd-page-comms-contacts">
            {sectorPlayers.map((pilot) => (
              <li key={pilot.user_id} className="mfd-page-comms-contact">
                {pilot.username || '—'}
              </li>
            ))}
          </ul>
        )}

        <div className="mfd-page-section-label">CREW</div>
        {playerState?.team_id ? (
          // Presence only — resolving the team name needs a fetch that v1
          // deliberately skips (contract: no team fetch).
          <MFDField label="AFFILIATION" value="ACTIVE" accent />
        ) : (
          <MFDEmpty text="NO CREW AFFILIATION" />
        )}
      </MFDPageBody>
    </div>
  );
};

export default React.memo(CommsCrewPage);
