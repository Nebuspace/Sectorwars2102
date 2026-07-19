import { useMemo } from 'react';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useGame } from '../../contexts/GameContext';

/**
 * contactClassification — shared sector-contact derivation + rep/law
 * classification (WO-HUD-LIGHTS phase 1), split out so the annunciator's
 * LAW/THREAT segments (useAnnunciatorState.ts) and TacticalTargetPage's
 * contact rows read the exact same classification instead of two
 * independently-maintained copies.
 *
 * `repBucket`/`LAW_ARCHETYPES` below are BYTE-EQUIVALENT ports of logic
 * TacticalTargetPage.tsx already ships (playerRepBucket/isHostileNpc/
 * repBucket, ~:131-151) and SolarSystemViewscreen.tsx's shipFaction() law
 * branch (~:337-338). This is a PHASE-1 EXTRACTION ONLY — TacticalTargetPage
 * keeps its own inline copy for now (a sibling WO owns that file this phase;
 * switching it to import this util is WO-HUD-LIGHTS phase 2). Until that
 * switch lands, any change to either inline source must be mirrored here by
 * hand — flagged, not silently risked.
 *
 * `deriveSectorContacts`/`useSectorContacts` are a byte-equivalent port of
 * GameDashboard.tsx's own `sectorContacts` useMemo (~:869-906) — merges live
 * WebSocket presence (`sectorPlayers`) with the sector snapshot
 * (`currentSector.players_present`), excluding self, de-duplicated.
 * GameDashboard's own inline copy is UNTOUCHED this phase (out of scope —
 * see WO-HUD-LIGHTS dispatch); this export exists so the annunciator (and,
 * later, other consumers) don't need a third copy of the merge.
 */

export interface SectorContact {
  player_id?: string;
  user_id?: string;
  id?: string;
  ship_id?: string;
  username?: string;
  name?: string;
  is_npc?: boolean;
  name_color?: string;
  military_rank?: string;
  reputation_tier?: string;
  personal_reputation?: number;
  /** NPC-only enrichment (npc_spawn_service._presence_entry / player.py). */
  archetype?: string;
  notoriety?: number;
  [key: string]: any;
}

export type RepBucket = 'red' | 'gray' | 'blue';

// Exact vocabulary TacticalTargetPage.tsx already ships for the reputation
// bucket (personal_reputation_service.py's 8-tier scale, bucketed to 3).
const RED_TIERS = new Set(['Villain', 'Criminal', 'Outlaw']);
const GRAY_TIERS = new Set(['Suspicious']);

const playerRepBucket = (tier?: string): RepBucket => {
  if (tier && RED_TIERS.has(tier)) return 'red';
  if (tier && GRAY_TIERS.has(tier)) return 'gray';
  return 'blue';
};

// Mirrors CombatInterface.tsx's npcStanding() "fair game" threshold, same
// as TacticalTargetPage.tsx's isHostileNpc().
const isHostileNpc = (contact: SectorContact): boolean => {
  const arch = String(contact.archetype || '').toUpperCase();
  if (arch === 'HOSTILE_RAIDER') return true;
  if (arch === 'LAW_ENFORCEMENT') return false;
  return typeof contact.notoriety === 'number' && contact.notoriety >= 50;
};

/** Byte-equivalent port of TacticalTargetPage.tsx's repBucket(). */
export const repBucket = (contact: SectorContact): RepBucket =>
  contact.is_npc ? (isHostileNpc(contact) ? 'red' : 'blue') : playerRepBucket(contact.reputation_tier);

/** Archetypes SolarSystemViewscreen's shipFaction() reads as law (blue,
 *  "not fair game") — LAW_ENFORCEMENT / FACTION_PATROL / STATION_SECURITY
 *  (SolarSystemViewscreen.tsx:337-338). */
export const LAW_ARCHETYPES: ReadonlySet<string> = new Set([
  'LAW_ENFORCEMENT',
  'FACTION_PATROL',
  'STATION_SECURITY',
]);

export const isLawArchetype = (contact: SectorContact): boolean =>
  LAW_ARCHETYPES.has(String(contact.archetype || '').toUpperCase());

/** True iff any contact in the list is a law-archetype NPC. */
export const hasLawContact = (contacts: SectorContact[]): boolean => contacts.some(isLawArchetype);

/** True iff any contact in the list buckets red (WANTED) or gray
 *  (GREY-FLAG) — never blue (CLEAR). */
export const hasWantedOrGreyContact = (contacts: SectorContact[]): boolean =>
  contacts.some((c) => {
    const bucket = repBucket(c);
    return bucket === 'red' || bucket === 'gray';
  });

interface SelfIdentity {
  id?: string | number;
  username?: string;
}

/** Pure merge — WS `sectorPlayers` ∪ API `players_present`, de-duplicated,
 *  excluding `self`. Byte-equivalent port of GameDashboard.tsx's
 *  `sectorContacts` useMemo body (~:869-906) — see that file's own
 *  doc-comment for the key-collision rationale (real players surface twice,
 *  NPCs carry no stable username so they key on player_id instead). */
export function deriveSectorContacts(
  sectorPlayers: SectorContact[] | null | undefined,
  playersPresent: SectorContact[] | null | undefined,
  self: SelfIdentity | null | undefined
): SectorContact[] {
  const contacts = new Map<string, SectorContact>();
  const addContact = (contact: SectorContact) => {
    if (!contact) return;
    const key = contact.is_npc
      ? String(contact.player_id || contact.user_id || contact.id || '')
      : String(
          (contact.username && contact.username.toLowerCase()) ||
          contact.user_id || contact.id || ''
        );
    if (!key) return;
    const isSelf = self && (
      key === String(self.id) ||
      (contact.username && self.username &&
       contact.username.toLowerCase() === self.username.toLowerCase())
    );
    if (isSelf) return;
    const existing = contacts.get(key);
    if (!existing) {
      contacts.set(key, contact);
    } else if (!existing.player_id && contact.player_id) {
      // Prefer the entry carrying player_id so the surviving row is
      // hailable — merge the snapshot's player_id (and richer fields) over
      // the bare WS-presence entry without losing either source.
      contacts.set(key, { ...existing, ...contact });
    }
  };
  (sectorPlayers || []).forEach(addContact);
  (playersPresent || []).forEach(addContact);
  return Array.from(contacts.values());
}

/** Hook form — reads `sectorPlayers`/`currentSector`/`playerState` directly
 *  from context (all context-level, per WO-HUD-LIGHTS) for consumers that
 *  don't already have the merged list threaded as a prop (the annunciator).
 *  GameDashboard keeps its own inline useMemo calling the same merge shape
 *  directly (untouched this phase, not switched to this hook). */
export function useSectorContacts(): SectorContact[] {
  const { sectorPlayers } = useWebSocket();
  const { currentSector, playerState } = useGame();
  return useMemo(
    () => deriveSectorContacts(sectorPlayers, currentSector?.players_present, playerState),
    [sectorPlayers, currentSector?.players_present, playerState]
  );
}
