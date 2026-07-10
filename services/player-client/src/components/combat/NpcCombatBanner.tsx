import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';
import './npc-combat-banner.css';

/**
 * NpcCombatBanner — the cockpit surface for the `npc_combat_initiated` WS
 * event (WO-CMB-NPC-INITIATED-1 lane D): an NPC — a Federation patrol
 * interdicting the player, or a pirate raider attacking them — engages.
 *
 * The backend sends the SAME frame shape twice per event: once personal-to-
 * defender, once sector-broadcast to spectators (combat_service). Only
 * `defender_id` tells them apart, and that comparison needs the current
 * player's id — WebSocketContext deliberately doesn't carry that (it never
 * imports GameContext), so this component is the one place both are
 * available (mirrors PriorityHailConsumer's useWebSocket + useGame pairing).
 *
 *   • DEFENDER match  — an unmistakable, manually-dismissed banner (not a
 *     toast that could scroll off the stack, and not an auto-timeout modal
 *     — this is an interrupt the pilot should notice, but must stay free to
 *     act while it's up, e.g. open the weapons console). Archetype-flavored
 *     copy: a lawful interdiction reads differently from a raider's ambush.
 *   • Spectator (not the defender) — the lighter sector-broadcast treatment,
 *     matching the established teammate_under_attack toast idiom exactly
 *     (same addNotification shape/phrasing style, 'warning' level).
 *
 * combat_id rides through the payload for hand-off correlation to whatever
 * eventually renders the resolved fight — building that flow is explicitly
 * OUT of scope here (player-client combat today is the synchronous,
 * REST-driven CombatInterface engage/resolve call, not a live WS round
 * stream; there is no existing "resume by combat_id" surface to hand off
 * to yet). This component only raises the alert.
 *
 * Mounted once in GameLayout, alongside MedalToast/PriorityHailConsumer/
 * WelcomeBackToast. Fixed top-of-viewport placement — SCROLL LAW: visible
 * without scrolling, an interrupt, not a log line.
 *
 * Copy is NO-CANON (flagged for design sign-off), matching WelcomeBackToast's
 * own caveat for hardcoded en text ahead of I18N-CORE landing.
 */
const NpcCombatBanner: React.FC = () => {
  const { npcCombatSignal, lastNpcCombatInitiated, addNotification } = useWebSocket();
  const { playerState } = useGame();

  const [visible, setVisible] = useState(false);
  const seenSignal = useRef(0);

  useEffect(() => {
    // signal 0 is the mount baseline (no event yet); only react to a genuine
    // new bump, never a re-render replaying the same signal.
    if (npcCombatSignal <= 0 || npcCombatSignal === seenSignal.current || !lastNpcCombatInitiated) {
      return;
    }
    seenSignal.current = npcCombatSignal;

    // playerState not loaded yet (e.g. a frame arrives before GameContext's
    // initial fetch resolves) -- can't safely tell defender from spectator,
    // so raise neither surface for this occurrence rather than guess.
    if (!playerState) return;

    const isDefender = lastNpcCombatInitiated.defender_id === playerState.id;

    if (isDefender) {
      setVisible(true);
      return;
    }

    // Spectator: the lighter sector-broadcast toast, matching
    // teammate_under_attack's exact phrasing idiom.
    const attackerName = lastNpcCombatInitiated.npc_display_name || 'An NPC vessel';
    const defenderName = lastNpcCombatInitiated.defender_name || 'a pilot';
    const sectorId = lastNpcCombatInitiated.sector_id;
    const isRaider = lastNpcCombatInitiated.npc_archetype === 'HOSTILE_RAIDER';
    addNotification({
      title: isRaider ? 'Pirate Raid Detected' : 'Patrol Interdiction',
      content: sectorId !== null
        ? `${attackerName} is attacking ${defenderName} in sector ${sectorId}`
        : `${attackerName} is attacking ${defenderName}`,
      level: 'warning'
    });
  }, [npcCombatSignal, lastNpcCombatInitiated, playerState, addNotification]);

  if (!visible || !lastNpcCombatInitiated) return null;

  const {
    npc_display_name: npcName,
    npc_ship_name: shipName,
    npc_ship_type: shipType,
    npc_archetype: archetype,
    sector_id: sectorId,
    combat_id: combatId
  } = lastNpcCombatInitiated;
  const isRaider = archetype === 'HOSTILE_RAIDER';

  const headline = isRaider
    ? `${npcName} has opened fire on your vessel!`
    : `${npcName} is moving to interdict your vessel.`;
  const shipLine = shipName
    ? `Contact: ${shipName}${shipType ? ` (${shipType})` : ''}`
    : null;

  return (
    <div
      className={`npc-combat-banner ${isRaider ? 'npc-combat-banner--raider' : 'npc-combat-banner--patrol'}`}
      role="alert"
      aria-live="assertive"
      data-combat-id={combatId}
    >
      <div className="npc-combat-banner-icon" aria-hidden="true">{isRaider ? '☠' : '⚖'}</div>
      <div className="npc-combat-banner-body">
        <div className="npc-combat-banner-eyebrow">
          {isRaider ? 'HOSTILE CONTACT' : 'LAWFUL INTERDICTION'}
        </div>
        <div className="npc-combat-banner-headline">{headline}</div>
        {shipLine && <div className="npc-combat-banner-detail">{shipLine}</div>}
        {sectorId !== null && (
          <div className="npc-combat-banner-detail">Sector {sectorId}</div>
        )}
      </div>
      <button
        className="npc-combat-banner-dismiss"
        onClick={() => setVisible(false)}
        aria-label="Dismiss combat alert"
      >
        ×
      </button>
    </div>
  );
};

export default NpcCombatBanner;
