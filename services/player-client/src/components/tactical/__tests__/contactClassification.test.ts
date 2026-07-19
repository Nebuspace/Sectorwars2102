// @vitest-environment jsdom
/**
 * contactClassification — pure unit tests (WO-HUD-LIGHTS phase 1). The
 * functions under test (repBucket/law-set/merge) are themselves pure and
 * DOM-free, but this module also exports `useSectorContacts`, which pulls
 * in the REAL WebSocketContext module (unmocked here) -- that module's
 * websocket.ts singleton touches `document` at import time, so jsdom is
 * required even though these specific tests never render a component
 * (mirrors the same constraint TacticalTargetPage.test.tsx and friends
 * work around). useSectorContacts (the hook form) is exercised indirectly
 * via Annunciator.test.tsx's mocked-context harness.
 */
import { describe, it, expect } from 'vitest';
import {
  repBucket,
  isLawArchetype,
  hasLawContact,
  hasWantedOrGreyContact,
  deriveSectorContacts,
  LAW_ARCHETYPES,
  type SectorContact,
} from '../contactClassification';

describe('repBucket', () => {
  it('buckets a player by reputation_tier — red/gray/blue vocabulary', () => {
    expect(repBucket({ is_npc: false, reputation_tier: 'Villain' })).toBe('red');
    expect(repBucket({ is_npc: false, reputation_tier: 'Criminal' })).toBe('red');
    expect(repBucket({ is_npc: false, reputation_tier: 'Outlaw' })).toBe('red');
    expect(repBucket({ is_npc: false, reputation_tier: 'Suspicious' })).toBe('gray');
    expect(repBucket({ is_npc: false, reputation_tier: 'Neutral' })).toBe('blue');
    expect(repBucket({ is_npc: false, reputation_tier: 'Legendary' })).toBe('blue');
    expect(repBucket({ is_npc: false })).toBe('blue'); // no tier -- defaults CLEAR
  });

  it('buckets an NPC by archetype/notoriety, never by reputation_tier', () => {
    expect(repBucket({ is_npc: true, archetype: 'HOSTILE_RAIDER' })).toBe('red');
    expect(repBucket({ is_npc: true, archetype: 'LAW_ENFORCEMENT' })).toBe('blue');
    expect(repBucket({ is_npc: true, archetype: 'TRADER', notoriety: 50 })).toBe('red');
    expect(repBucket({ is_npc: true, archetype: 'TRADER', notoriety: 49 })).toBe('blue');
    expect(repBucket({ is_npc: true, archetype: 'TRADER' })).toBe('blue');
    // reputation_tier present but irrelevant for an NPC.
    expect(repBucket({ is_npc: true, archetype: 'TRADER', reputation_tier: 'Villain' })).toBe('blue');
  });
});

describe('isLawArchetype / LAW_ARCHETYPES', () => {
  it('matches exactly the three law archetypes (case-insensitive)', () => {
    expect(LAW_ARCHETYPES.size).toBe(3);
    expect(isLawArchetype({ archetype: 'LAW_ENFORCEMENT' })).toBe(true);
    expect(isLawArchetype({ archetype: 'FACTION_PATROL' })).toBe(true);
    expect(isLawArchetype({ archetype: 'STATION_SECURITY' })).toBe(true);
    expect(isLawArchetype({ archetype: 'law_enforcement' })).toBe(true); // case-insensitive
    expect(isLawArchetype({ archetype: 'HOSTILE_RAIDER' })).toBe(false);
    expect(isLawArchetype({ archetype: 'TRADER' })).toBe(false);
    expect(isLawArchetype({})).toBe(false);
  });
});

describe('hasLawContact / hasWantedOrGreyContact', () => {
  const lawNpc: SectorContact = { player_id: 'npc-1', is_npc: true, archetype: 'LAW_ENFORCEMENT' };
  const raiderNpc: SectorContact = { player_id: 'npc-2', is_npc: true, archetype: 'HOSTILE_RAIDER' };
  const suspiciousPlayer: SectorContact = { user_id: 'p-1', is_npc: false, reputation_tier: 'Suspicious' };
  const cleanPlayer: SectorContact = { user_id: 'p-2', is_npc: false, reputation_tier: 'Lawful' };

  it('hasLawContact is true iff a law-archetype contact is present', () => {
    expect(hasLawContact([lawNpc, cleanPlayer])).toBe(true);
    expect(hasLawContact([raiderNpc, cleanPlayer])).toBe(false);
    expect(hasLawContact([])).toBe(false);
  });

  it('hasWantedOrGreyContact is true iff a red or gray bucket contact is present, never for blue alone', () => {
    expect(hasWantedOrGreyContact([raiderNpc])).toBe(true);
    expect(hasWantedOrGreyContact([suspiciousPlayer])).toBe(true);
    expect(hasWantedOrGreyContact([cleanPlayer, lawNpc])).toBe(false);
    expect(hasWantedOrGreyContact([])).toBe(false);
  });
});

describe('deriveSectorContacts', () => {
  const self = { id: 'player-1', username: 'Commander' };

  it('merges WS presence and API snapshot, de-duplicating by normalized username for real players', () => {
    const sectorPlayers: SectorContact[] = [{ user_id: 'u-2', username: 'Shifty' }];
    const playersPresent: SectorContact[] = [
      { user_id: 'u-2', username: 'SHIFTY', player_id: 'p-2', reputation_tier: 'Suspicious' },
    ];
    const result = deriveSectorContacts(sectorPlayers, playersPresent, self);
    expect(result).toHaveLength(1);
    // The merged row carries the richer snapshot fields (player_id, reputation_tier).
    expect(result[0].player_id).toBe('p-2');
    expect(result[0].reputation_tier).toBe('Suspicious');
  });

  it('keys NPCs on player_id, never merging two distinct same-named NPCs', () => {
    const playersPresent: SectorContact[] = [
      { player_id: 'npc-a', is_npc: true, username: 'Captain', archetype: 'TRADER' },
      { player_id: 'npc-b', is_npc: true, username: 'Captain', archetype: 'HOSTILE_RAIDER' },
    ];
    const result = deriveSectorContacts([], playersPresent, self);
    expect(result).toHaveLength(2);
  });

  it('excludes self by id and by case-insensitive username match', () => {
    const playersPresent: SectorContact[] = [
      { user_id: 'player-1', username: 'Commander' },
      { user_id: 'other-id', username: 'COMMANDER' }, // same name, different id -- still self by username match
      { user_id: 'u-3', username: 'Wingman' },
    ];
    const result = deriveSectorContacts([], playersPresent, self);
    expect(result).toHaveLength(1);
    expect(result[0].username).toBe('Wingman');
  });

  it('returns an empty list for empty/undefined inputs', () => {
    expect(deriveSectorContacts([], [], self)).toEqual([]);
    expect(deriveSectorContacts(undefined, null, self)).toEqual([]);
    expect(deriveSectorContacts(undefined, undefined, null)).toEqual([]);
  });
});
