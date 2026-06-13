/**
 * COMMS / CREW — MFD ops page (status: partial).
 *
 * Sector contacts merge live WebSocket presence (sectorPlayers, human pilots)
 * with the API sector snapshot (currentSector.players_present, which also
 * carries NPC presence entries) — the same source the main cockpit COMMS
 * uses. Without the snapshot the page was blind to NPCs, so a sector full of
 * patrolling marshals showed "no contacts". Uplink + unread + crew
 * affiliation come from GameContext/WebSocketContext. Passive: no hail
 * composer here (the COMMS station owns messaging).
 */

import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty } from '../atoms';
import './pages-ops.css';

const ACCENT = '#00FF7F';

const CommsCrewPage: React.FC = () => {
  const { playerState, currentSector, unreadMessageCount } = useGame();
  const { isConnected, sectorPlayers } = useWebSocket();

  // Merge WS presence + API snapshot, drop self, de-dupe. Mirrors the main
  // COMMS contact merge (GameDashboard.sectorContacts): real pilots key on
  // lowercased username (they appear in both sources); NPC entries key on
  // their NPCCharacter id (player_id) since same-named captains must stay
  // distinct and they have no username.
  const contacts = React.useMemo(() => {
    const map = new Map<string, any>();
    const add = (c: any) => {
      if (!c) return;
      const key = c.is_npc
        ? String(c.player_id || c.user_id || c.id || '')
        : String((c.username && c.username.toLowerCase()) || c.user_id || c.id || '');
      if (!key) return;
      const isSelf = playerState && (
        key === String(playerState.id) ||
        (c.username && (playerState as any).username &&
          c.username.toLowerCase() === (playerState as any).username.toLowerCase())
      );
      if (isSelf) return;
      const existing = map.get(key);
      if (!existing) {
        map.set(key, c);
      } else if (!existing.player_id && c.player_id) {
        map.set(key, { ...existing, ...c });
      }
    };
    sectorPlayers.forEach(add);
    ((currentSector as any)?.players_present || []).forEach(add);
    return Array.from(map.values());
  }, [sectorPlayers, currentSector, playerState]);

  return (
    <div className="mfd-page-ops">
      <MFDPageHeader title="COMMS / CREW" accent={ACCENT} status="partial" />
      <MFDPageBody scrollKey="comms-crew">
        <MFDField label="UPLINK" value={isConnected ? 'LINK OK' : 'LINK DOWN'} accent={isConnected} />
        <MFDField label="UNREAD" value={unreadMessageCount ?? '—'} />

        <div className="mfd-page-section-label">CONTACTS IN SECTOR</div>
        {contacts.length === 0 ? (
          <MFDEmpty text="NO CONTACTS IN SECTOR" />
        ) : (
          <ul className="mfd-page-comms-contacts">
            {contacts.map((c) => {
              const name = c.username || c.name || 'UNKNOWN CONTACT';
              const key = (c.is_npc && c.player_id) || c.user_id || c.id || name;
              return (
                <li key={key} className="mfd-page-comms-contact">
                  <span>{name}</span>
                  {c.is_npc && <span className="mfd-page-npc-badge">NPC</span>}
                </li>
              );
            })}
          </ul>
        )}

        <div className="mfd-page-section-label">CREW</div>
        {playerState?.team_id ? (
          // Presence only — resolving the team name needs a fetch v1 skips.
          <MFDField label="AFFILIATION" value="ACTIVE" accent />
        ) : (
          <MFDEmpty text="NO CREW AFFILIATION" />
        )}
      </MFDPageBody>
    </div>
  );
};

export default React.memo(CommsCrewPage);
