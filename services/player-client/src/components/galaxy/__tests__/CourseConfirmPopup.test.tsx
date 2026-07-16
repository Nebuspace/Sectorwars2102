import { describe, it, expect } from 'vitest';
import {
  hopRiskBand,
  summarizeRouteRisk,
  ariaEngageCommand,
} from '../CourseConfirmPopup';
import type { CourseHop } from '../../../contexts/AutopilotContext';

function hop(partial: Partial<CourseHop> & Pick<CourseHop, 'sector_id' | 'name'>): CourseHop {
  return {
    turn_cost: 1,
    visited: true,
    safety_rating: 0.9,
    via_tunnel: false,
    ...partial,
  };
}

describe('CourseConfirmPopup risk helpers', () => {
  it('marks unvisited hops as UNKNOWN — never fabricates safety', () => {
    expect(hopRiskBand(hop({
      sector_id: 2,
      name: 'Remote',
      visited: false,
      safety_rating: null,
    }))).toBe('UNKNOWN');
  });

  it('summarizes mixed routes as UNCHARTED CONDITIONS when any hop is unknown', () => {
    const summary = summarizeRouteRisk([
      hop({ sector_id: 2, name: 'Safe', safety_rating: 0.9 }),
      hop({ sector_id: 3, name: 'Fog', visited: false, safety_rating: null }),
    ]);
    expect(summary.band).toBe('UNKNOWN');
    expect(summary.label).toBe('UNCHARTED CONDITIONS');
    expect(summary.unvisitedCount).toBe(1);
  });

  it('prefers HOSTILE over unknown when a visited leg is dangerous', () => {
    const summary = summarizeRouteRisk([
      hop({ sector_id: 2, name: 'Bad', safety_rating: 0.1 }),
      hop({ sector_id: 3, name: 'Fog', visited: false, safety_rating: null }),
    ]);
    expect(summary.band).toBe('HOSTILE');
  });

  it('builds the standard ARIA engage command', () => {
    expect(ariaEngageCommand(214)).toBe(
      'ARIA, engage plotted course to Sector 214.',
    );
  });
});
