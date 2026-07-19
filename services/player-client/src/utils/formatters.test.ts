import { describe, it, expect } from 'vitest';
import { formatRegionType } from './formatters';

describe('formatRegionType', () => {
  it('formats "terran_space" (real RegionType enum value) to "Terran Space"', () => {
    expect(formatRegionType('terran_space')).toBe('Terran Space');
  });

  it('formats "player_owned" (real RegionType enum value, a second region_type) to "Player Owned"', () => {
    expect(formatRegionType('player_owned')).toBe('Player Owned');
  });

  it('formats "central_nexus" (real RegionType enum value) to "Central Nexus"', () => {
    expect(formatRegionType('central_nexus')).toBe('Central Nexus');
  });

  it('replaces every underscore, not just the first', () => {
    expect(formatRegionType('a_b_c')).toBe('A B C');
  });

  it('returns null for a null region_type (player with no region) rather than a guess', () => {
    expect(formatRegionType(null)).toBeNull();
  });

  it('returns null for an undefined region_type rather than a guess', () => {
    expect(formatRegionType(undefined)).toBeNull();
  });

  it('returns null for an empty-string region_type rather than a guess', () => {
    expect(formatRegionType('')).toBeNull();
  });
});
