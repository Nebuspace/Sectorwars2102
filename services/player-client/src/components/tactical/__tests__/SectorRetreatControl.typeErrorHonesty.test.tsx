// @vitest-environment jsdom
/**
 * LEG-3159 Soft-ORDER — SectorRetreatControl TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SectorRetreatControl, { formatSectorRetreatError } from '../SectorRetreatControl';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const retreatFromSector = vi.fn();
const refreshPlayerState = vi.fn();
const getAvailableMoves = vi.fn();

let mockPlayerState: {
  is_docked?: boolean;
  is_landed?: boolean;
} | null = { is_docked: false, is_landed: false };

vi.mock('../../../services/api', () => ({
  combatAPI: {
    retreatFromSector: (...args: unknown[]) => retreatFromSector(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    refreshPlayerState,
    getAvailableMoves,
  }),
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatSectorRetreatError TypeError densify (LEG-3159)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatSectorRetreatError(new TypeError('Failed to fetch'), 'Sector retreat failed');
    expect(text).toBe('Sector retreat failed');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3296)', () => {
    expect(formatSectorRetreatError(new Error('Network Error'), 'Sector retreat failed')).toBe(
      'Sector retreat failed',
    );
    expect(formatSectorRetreatError(new Error('Failed to fetch'), 'Sector retreat failed')).toBe(
      'Sector retreat failed',
    );
    expect(formatSectorRetreatError(new Error('   '), 'Sector retreat failed')).toBe(
      'Sector retreat failed',
    );
  });

  it('preserves server detail for non-TypeError errors', () => {
    expect(formatSectorRetreatError(new Error('Not enough turns'), 'Sector retreat failed')).toBe(
      'Not enough turns',
    );
  });
});

describe('SectorRetreatControl retreat TypeError densify (LEG-3159)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    retreatFromSector.mockReset();
    refreshPlayerState.mockReset();
    getAvailableMoves.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    getAvailableMoves.mockResolvedValue(undefined);
    mockPlayerState = { is_docked: false, is_landed: false };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('retreat TypeError surfaces fallback without Failed to fetch / TypeError', async () => {
    retreatFromSector.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<SectorRetreatControl />);
    });

    const btn = container.querySelector('[data-testid="sector-retreat-flee"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const msg = container.querySelector('[data-testid="sector-retreat-msg"]');
    expect(msg?.textContent).toBe('Sector retreat failed');
    expect(msg?.textContent).not.toMatch(/Failed to fetch/i);
    expect(msg?.textContent).not.toMatch(/TypeError/i);
  });
});

describe('formatSectorRetreatError 403/429 densify (LEG-4080)', () => {
  const FALLBACK = 'Sector retreat failed';
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatSectorRetreatError(apiRequestError(403), FALLBACK)).toMatch(/permission/i);
    expect(formatSectorRetreatError(apiRequestError(403, 'retreat_denied'), FALLBACK)).toBe(
      'retreat_denied',
    );
    expect(formatSectorRetreatError(apiRequestError(429), FALLBACK)).toMatch(/rate limit/i);
    expect(formatSectorRetreatError(apiRequestError(429), FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatSectorRetreatError(apiRequestError(403), FALLBACK)).not.toMatch(/TypeError/i);
  });
});
