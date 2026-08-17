// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockRefine, mockStart, mockStatus, mockCollect } = vi.hoisted(() => ({
  mockRefine: vi.fn(),
  mockStart: vi.fn(),
  mockStatus: vi.fn(),
  mockCollect: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    refiningAPI: {
      refine: (...args: unknown[]) => mockRefine(...args),
      startLumen: (...args: unknown[]) => mockStart(...args),
      lumenStatus: (...args: unknown[]) => mockStatus(...args),
      collectLumen: (...args: unknown[]) => mockCollect(...args),
    },
  };
});

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({
    refreshPlayerState: vi.fn(async () => {}),
  }),
}));

import CrystalRefiningPanel from './CrystalRefiningPanel';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('CrystalRefiningPanel', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockStatus.mockResolvedValue({ pending: false, ready_at: null, collectible: false });
    mockRefine.mockResolvedValue({ quantum_crystals: 3, message: 'ok' });
    mockStart.mockResolvedValue({
      lumen_refine_ready_at: new Date(Date.now() + 3600_000).toISOString(),
    });
    mockCollect.mockResolvedValue({ message: 'collected' });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('calls refiningAPI.refine on crystal button', async () => {
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
    expect(mockRefine).toHaveBeenCalled();
  });

  it('starts lumen refine and shows countdown', async () => {
    const readyAt = new Date(Date.now() + 3600_000).toISOString();
    mockStatus
      .mockResolvedValueOnce({ pending: false, ready_at: null, collectible: false })
      .mockResolvedValue({ pending: true, ready_at: readyAt, collectible: false });
    mockStart.mockResolvedValue({ lumen_refine_ready_at: readyAt });

    await act(async () => {
      root.render(
        <CrystalRefiningPanel shards={120} crystals={2} isDocked onBalancesChanged={vi.fn()} />,
      );
      await flush();
    });
    const btn = container.querySelector('[data-testid="lumen-start-btn"]') as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });
    expect(mockStart).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="lumen-countdown"]')).toBeTruthy();
  });

  it('collects when lumen is collectible', async () => {
    mockStatus.mockResolvedValue({
      pending: true,
      ready_at: new Date(Date.now() - 1000).toISOString(),
      collectible: true,
    });
    await act(async () => {
      root.render(
        <CrystalRefiningPanel shards={0} crystals={2} isDocked onBalancesChanged={vi.fn()} />,
      );
      await flush();
      await flush();
    });
    const btn = container.querySelector('[data-testid="lumen-collect-btn"]') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });
    expect(mockCollect).toHaveBeenCalled();
  });
});
