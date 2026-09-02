import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  CombatActivityChart,
  combatActivityLoadError,
  COMBAT_ACTIVITY_LOAD_FALLBACK,
} from './CombatActivityChart';

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3790 Soft-ORDER — CombatActivityChart TypeError/Network Error densify.
 * Chart is presentational; parent fetch uses combatActivityLoadError for operator-safe copy.
 */
describe('combatActivityLoadError formatter (LEG-3790)', () => {
  it('collapses TypeError Failed to fetch to operator-safe fallback', () => {
    const text = combatActivityLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe(COMBAT_ACTIVITY_LOAD_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses axios Network Error to operator-safe fallback', () => {
    const text = combatActivityLoadError(new Error('Network Error'));
    expect(text).toBe(COMBAT_ACTIVITY_LOAD_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('collapses Error Failed to fetch to operator-safe fallback', () => {
    const text = combatActivityLoadError(new Error('Failed to fetch'));
    expect(text).toBe(COMBAT_ACTIVITY_LOAD_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail', () => {
    const text = combatActivityLoadError({
      response: { status: 500, data: { detail: 'Combat analytics temporarily disabled.' } },
    });
    expect(text).toBe('Combat analytics temporarily disabled.');
  });
});

describe('CombatActivityChart typeErrorHonesty densify (LEG-3790)', () => {
  it('renders empty-state chart without leaking transport error strings', () => {
    render(<CombatActivityChart events={[]} />);

    expect(screen.getByText('Combat Activity (Last Hour)')).toBeTruthy();
    const bodyText = document.body.textContent ?? '';
    assertNoTransportLeak(bodyText);
  });

  it('renders with partial events without surfacing transport-shaped payload text', () => {
    const events = [
      {
        id: 'evt-1',
        started_at: new Date().toISOString(),
        combat_stats: { damageDealt: 0, damageReceived: 0 },
        error_message: 'Network Error',
      },
      {
        id: 'evt-2',
        detail: 'TypeError: Failed to fetch',
      },
    ];

    render(<CombatActivityChart events={events} />);

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).toContain('Combat Activity (Last Hour)');
    assertNoTransportLeak(bodyText);
  });
});
