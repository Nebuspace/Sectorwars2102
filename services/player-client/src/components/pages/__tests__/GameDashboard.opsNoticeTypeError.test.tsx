// @vitest-environment jsdom
/**
 * LEG-3331 Soft-ORDER — GameDashboard planetary-ops TypeError densify.
 */
import { describe, expect, it } from 'vitest';
import { formatGameDashboardOpsError } from '../GameDashboard';

describe('formatGameDashboardOpsError shield/citadel cluster (LEG-3331)', () => {
  it('maps TypeError Failed to fetch to shield upgrade fallback', () => {
    const text = formatGameDashboardOpsError(
      new TypeError('Failed to fetch'),
      'Shield generator upgrade failed',
    );
    expect(text).toBe('Shield generator upgrade failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('keeps citadel upgrade gameserver axios detail honesty', () => {
    const err = {
      response: { data: { detail: 'Defense grid prerequisite not met' } },
    };
    expect(formatGameDashboardOpsError(err, 'Citadel upgrade failed')).toBe(
      'Defense grid prerequisite not met',
    );
  });
});

describe('formatGameDashboardOpsError vault cluster (LEG-3331)', () => {
  it('maps network collapse to vault transaction fallback', () => {
    const err = { message: 'Failed to fetch' };
    const text = formatGameDashboardOpsError(err, 'Vault transaction failed');
    expect(text).toBe('Vault transaction failed');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('keeps safe deposit server detail verbatim', () => {
    const err = {
      response: { data: { detail: 'Safe capacity exceeded' } },
    };
    expect(formatGameDashboardOpsError(err, 'Vault transaction failed')).toBe(
      'Safe capacity exceeded',
    );
  });
});

describe('formatGameDashboardOpsError shield/vault axios transport collapse (LEG-3352)', () => {
  it('falls back on axios Network Error for shield upgrade', () => {
    expect(
      formatGameDashboardOpsError(new Error('Network Error'), 'Shield generator upgrade failed'),
    ).toBe('Shield generator upgrade failed');
    expect(
      formatGameDashboardOpsError(new Error('Network Error'), 'Shield generator upgrade failed'),
    ).not.toBe('Network Error');
  });

  it('falls back on axios Failed to fetch for citadel upgrade', () => {
    expect(formatGameDashboardOpsError(new Error('Failed to fetch'), 'Citadel upgrade failed')).toBe(
      'Citadel upgrade failed',
    );
    expect(formatGameDashboardOpsError(new Error('Failed to fetch'), 'Citadel upgrade failed')).not.toMatch(
      /Failed to fetch/i,
    );
  });

  it('falls back on whitespace-only message for vault transaction', () => {
    expect(formatGameDashboardOpsError(new Error('   '), 'Vault transaction failed')).toBe(
      'Vault transaction failed',
    );
    expect(formatGameDashboardOpsError({ message: '  \t  ' }, 'Vault transaction failed')).toBe(
      'Vault transaction failed',
    );
  });
});

describe('formatGameDashboardOpsError colonist/rename cluster (LEG-3331)', () => {
  it('maps TypeError to colonist transfer fallback', () => {
    const text = formatGameDashboardOpsError(
      new TypeError('Failed to fetch'),
      'Colonist transfer failed',
    );
    expect(text).toBe('Colonist transfer failed');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('maps TypeError to planet rename fallback', () => {
    const text = formatGameDashboardOpsError(
      new TypeError('Failed to fetch'),
      'Failed to rename planet. Please try again.',
    );
    expect(text).toBe('Failed to rename planet. Please try again.');
    expect(text).not.toMatch(/TypeError/i);
  });
});
