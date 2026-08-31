// @vitest-environment jsdom
/**
 * LEG-3144 — salvage-break API CTA + TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockSalvageBreak = vi.fn();
vi.mock('../../../services/api', () => ({
  shipRegistryAPI: {
    salvageBreak: (...a: unknown[]) => mockSalvageBreak(...a),
  },
}));

import SalvageBreakCta, {
  formatSalvageBreakError,
  isSalvageBreakEligibleContact,
} from '../SalvageBreakCta';

describe('SalvageBreakCta (LEG-3144)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockSalvageBreak.mockReset();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('formatSalvageBreakError hides TypeError Failed to fetch', () => {
    const text = formatSalvageBreakError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/check your connection/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('isSalvageBreakEligibleContact requires drifting pin-locked hull', () => {
    expect(
      isSalvageBreakEligibleContact(
        { ship_id: 'h1', pin_locked: true, is_drifting: true } as never,
        'me',
      ),
    ).toBe(true);
    expect(
      isSalvageBreakEligibleContact(
        { ship_id: 'h1', pin_locked: true, player_id: 'me' } as never,
        'me',
      ),
    ).toBe(false);
  });

  it('happy path: POST salvage-break and show in-progress ETA', async () => {
    mockSalvageBreak.mockResolvedValue({
      ship_id: 'h1',
      completes_at: new Date(Date.now() + 3600_000).toISOString(),
    });
    await act(async () => {
      root.render(<SalvageBreakCta shipId="h1" shipName="Drifter" />);
    });
    await flush();
    const btn = container.querySelector('.salvage-break-btn') as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    await flush();
    expect(mockSalvageBreak).toHaveBeenCalledWith('h1');
    expect(container.querySelector('[data-testid="salvage-break-in-progress"]')).toBeTruthy();
  });

  it('surfaces ERR_SALVAGE_BREAK_IN_PROGRESS honestly', () => {
    const text = formatSalvageBreakError({
      code: 'ERR_SALVAGE_BREAK_IN_PROGRESS',
      data: { detail: { code: 'ERR_SALVAGE_BREAK_IN_PROGRESS', completes_at: '2099-01-01T12:00:00Z' } },
    });
    expect(text).toMatch(/already in progress/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});
