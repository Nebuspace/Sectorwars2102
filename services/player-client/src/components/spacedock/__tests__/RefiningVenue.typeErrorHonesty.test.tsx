// @vitest-environment jsdom
/**
 * LEG-3138 Soft-ORDER — RefiningVenue / CrystalRefiningPanel TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CrystalRefiningPanel, { formatCrystalRefiningError } from '../../quantum/CrystalRefiningPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockRefine, mockStatus } = vi.hoisted(() => ({
  mockRefine: vi.fn(),
  mockStatus: vi.fn(),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    refiningAPI: {
      refine: (...args: unknown[]) => mockRefine(...args),
      startLumen: vi.fn(),
      lumenStatus: (...args: unknown[]) => mockStatus(...args),
      collectLumen: vi.fn(),
    },
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    refreshPlayerState: vi.fn(async () => {}),
  }),
}));

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('formatCrystalRefiningError TypeError densify (LEG-3138)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatCrystalRefiningError(new TypeError('Failed to fetch'), 'Crystal refine rejected.');
    expect(text).toBe('Crystal refine rejected.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    const err = Object.assign(new Error('insufficient shards'), {
      response: { data: { detail: 'Need 5 shards to refine.' } },
    });
    expect(formatCrystalRefiningError(err, 'Crystal refine rejected.')).toBe('Need 5 shards to refine.');
  });
});

describe('CrystalRefiningPanel refine TypeError densify (LEG-3138)', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockStatus.mockResolvedValue({ pending: false, ready_at: null, collectible: false });
    mockRefine.mockRejectedValue(new TypeError('Failed to fetch'));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('crystal refine TypeError surfaces honest fallback without Failed to fetch', async () => {
    await act(async () => {
      root.render(
        <CrystalRefiningPanel shards={10} crystals={2} isDocked onBalancesChanged={vi.fn()} />,
      );
      await flush();
    });

    const btn = container.querySelector('[data-testid="refine-crystal-btn"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(mockRefine).toHaveBeenCalled();
    });

    const errorEl = container.querySelector('[data-testid="crystal-refine-error"]');
    expect(errorEl?.textContent).toBe('Crystal refine rejected.');
    expect(errorEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errorEl?.textContent).not.toMatch(/TypeError/i);
  });
});
