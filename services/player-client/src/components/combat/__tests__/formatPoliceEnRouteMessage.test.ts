import { describe, it, expect } from 'vitest';
import { formatPoliceEnRouteMessage } from '../formatPoliceEnRouteMessage';
import type { PendingEngagementSummary } from '../../../services/pendingEngagementApi';

const base: PendingEngagementSummary = {
  id: 'pe-1',
  jurisdiction: null,
  offense_type: null,
  squad: ['Backup Squad'],
  officer_names: ['Marshal Vance'],
  turns_to_arrival: 2,
  grace_window: null,
};

describe('formatPoliceEnRouteMessage', () => {
  it('uses officer name and plural turns', () => {
    expect(formatPoliceEnRouteMessage(base)).toBe(
      'Marshal Vance is en route — 2 turns to arrival'
    );
  });

  it('uses singular turn at 1', () => {
    expect(
      formatPoliceEnRouteMessage({ ...base, turns_to_arrival: 1 })
    ).toBe('Marshal Vance is en route — 1 turn to arrival');
  });

  it('falls back to squad then generic label', () => {
    expect(
      formatPoliceEnRouteMessage({ ...base, officer_names: [] })
    ).toBe('Backup Squad is en route — 2 turns to arrival');
    expect(
      formatPoliceEnRouteMessage({ ...base, officer_names: [], squad: [] })
    ).toBe('Law enforcement is en route — 2 turns to arrival');
  });
});
