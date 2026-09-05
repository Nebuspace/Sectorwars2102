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
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

/**
 * LEG-3790 Soft-ORDER — CombatActivityChart TypeError/Network Error densify.
 * Chart is presentational; parent fetch uses combatActivityLoadError for operator-safe copy.
 * LEG-3896 Soft-ORDER — 403/429 HTTP honesty densify.
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

  it('surfaces 403 with Access denied copy (no raw HTTP 403)', () => {
    const text = combatActivityLoadError(axiosError(403));
    expect(text).toMatch(/Access denied/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy (no raw HTTP 429)', () => {
    const text = combatActivityLoadError(axiosError(429));
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
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
