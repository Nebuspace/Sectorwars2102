/**
 * LEG-3145 — landed colony vitals phase + growth/day formatting.
 */
import { describe, it, expect } from 'vitest';
import { formatColonyGrowthPerDay, formatColonyPhase } from '../colonyVitals';

describe('colony vitals (LEG-3145)', () => {
  it('maps citadel levels to lifecycle phase labels', () => {
    expect(formatColonyPhase(1)).toBe('Outpost');
    expect(formatColonyPhase(3)).toBe('Colony');
    expect(formatColonyPhase(5)).toBe('Planetary Capital');
    expect(formatColonyPhase(0)).toBe('Unestablished');
  });

  it('formats productionRates.colonists as signed /day growth', () => {
    expect(formatColonyGrowthPerDay(12.4)).toBe('+12/day');
    expect(formatColonyGrowthPerDay(-2.5)).toBe('-2.5/day');
    expect(formatColonyGrowthPerDay(0)).toBe('0/day');
  });
});
