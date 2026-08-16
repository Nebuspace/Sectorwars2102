// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockPlace, mockAvailable, mockOnTarget, mockCancel } = vi.hoisted(() => ({
  mockPlace: vi.fn(),
  mockAvailable: vi.fn(),
  mockOnTarget: vi.fn(),
  mockCancel: vi.fn(),
}));

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return {
    ...actual,
    bountyAPI: {
      place: (...a: unknown[]) => mockPlace(...a),
      getAvailable: (...a: unknown[]) => mockAvailable(...a),
      getOnTarget: (...a: unknown[]) => mockOnTarget(...a),
      cancel: (...a: unknown[]) => mockCancel(...a),
    },
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: 'me', credits: 50_000 },
    refreshPlayerState: vi.fn(async () => {}),
  }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ bountyEventSignal: 0 }),
}));

import BountyBoardPanel from './BountyBoardPanel';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('BountyBoardPanel', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockAvailable.mockResolvedValue([
      { bounty_id: 'b1', target_id: 't1', target_name: 'Target', amount: 2000, placed_by: 'me' },
    ]);
    mockPlace.mockResolvedValue({ success: true });
    mockCancel.mockResolvedValue({ success: true });
    mockOnTarget.mockResolvedValue([]);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('lists available bounties and places one', async () => {
    await act(async () => {
      root.render(<BountyBoardPanel />);
      await flush();
      await flush();
    });
    expect(container.querySelector('[data-testid="bounty-available-list"]')).toBeTruthy();

    const target = container.querySelector(
      '[data-testid="bounty-place-target"]',
    ) as HTMLInputElement;
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => {
      nativeSetter?.call(target, 'other-player');
      target.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="bounty-place-btn"]') as HTMLButtonElement).click();
      await flush();
      await flush();
    });
    expect(mockPlace).toHaveBeenCalledWith('other-player', 1000);
  });

  it('cancels own bounty', async () => {
    await act(async () => {
      root.render(<BountyBoardPanel />);
      await flush();
      await flush();
    });
    const btn = container.querySelector('[data-testid="bounty-cancel-b1"]') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });
    expect(mockCancel).toHaveBeenCalledWith('b1', 't1');
  });
});
