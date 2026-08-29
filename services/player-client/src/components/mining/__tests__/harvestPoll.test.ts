import { describe, expect, it } from 'vitest';
import { formatHarvestCountdown, isTerminalHarvestStatus } from '../harvestPoll';

describe('harvestPoll helpers (LEG-2731)', () => {
  it('isTerminalHarvestStatus treats PENDING and in_progress as non-terminal', () => {
    expect(isTerminalHarvestStatus('PENDING')).toBe(false);
    expect(isTerminalHarvestStatus('in_progress')).toBe(false);
    expect(isTerminalHarvestStatus('COMPLETED')).toBe(true);
    expect(isTerminalHarvestStatus('INTERRUPTED')).toBe(true);
    expect(isTerminalHarvestStatus('CANCELLED')).toBe(true);
  });

  it('formatHarvestCountdown shows seconds remaining', () => {
    const now = Date.parse('2026-08-28T12:00:00Z');
    expect(formatHarvestCountdown('2026-08-28T12:00:45Z', now)).toBe('~45s remaining');
  });

  it('formatHarvestCountdown shows completing when past resolves_at', () => {
    const now = Date.parse('2026-08-28T12:01:00Z');
    expect(formatHarvestCountdown('2026-08-28T12:00:00Z', now)).toBe('completing…');
  });
});
