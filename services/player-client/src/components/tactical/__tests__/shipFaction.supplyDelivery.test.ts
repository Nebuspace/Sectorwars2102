/**
 * LEG-1076 — supply_delivery mission must read as a distinct cue vs ordinary
 * commerce traders (windshield + system-view share shipFaction).
 */
import { describe, expect, it } from 'vitest';
import { shipFaction } from '../SolarSystemViewscreen';

describe('shipFaction supply_delivery cue (LEG-1076)', () => {
  it('labels supply_delivery traders SUPPLY HAUL (not REPUTABLE MERCHANT)', () => {
    const haul = shipFaction({
      is_npc: true,
      archetype: 'TRADER',
      mission: 'supply_delivery',
      notoriety: 0,
      ship_name: 'Restock Hauler',
    });
    const ordinary = shipFaction({
      is_npc: true,
      archetype: 'TRADER',
      mission: 'commerce',
      notoriety: 0,
      ship_name: 'Freight Captain',
    });
    expect(haul.key).toBe('supply_haul');
    expect(haul.label).toBe('SUPPLY HAUL');
    expect(ordinary.label).toBe('REPUTABLE MERCHANT');
    expect(haul.label).not.toBe(ordinary.label);
    expect(haul.color).not.toBe(ordinary.color);
  });

  it('does not override LAW / HOSTILE archetypes even if mission is stamped', () => {
    expect(
      shipFaction({
        is_npc: true,
        archetype: 'LAW_ENFORCEMENT',
        mission: 'supply_delivery',
      }).key,
    ).toBe('law');
    expect(
      shipFaction({
        is_npc: true,
        archetype: 'HOSTILE_RAIDER',
        mission: 'supply_delivery',
      }).key,
    ).toBe('raider');
  });

  it('treats missing mission as ordinary trader (commerce default on GS enrich)', () => {
    const fac = shipFaction({
      is_npc: true,
      archetype: 'TRADER',
      notoriety: 10,
    });
    expect(fac.key).toBe('reputable');
    expect(fac.label).toBe('REPUTABLE MERCHANT');
  });
});
