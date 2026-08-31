// @vitest-environment jsdom
/**
 * BountyBoard — LEG-156 loaded / empty / error / optional-omitted / realtime refresh.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getAvailable = vi.fn();

vi.mock('../../../services/api', () => ({
  bountyAPI: {
    getAvailable: (...args: unknown[]) => getAvailable(...args),
  },
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    bountyEventSignal: 0,
  }),
}));

import BountyBoard, { formatBountyBoardLoadError } from '../BountyBoard';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const SAMPLE = {
  player_id: 'p1',
  player_name: 'Rogue',
  reputation_tier: 'Villain',
  total_bounty: 137000,
  bounty_count: 4,
  current_sector: 442,
};

describe('BountyBoard', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getAvailable.mockReset();
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

  it('shows loading then loaded rows with API fields', async () => {
    getAvailable.mockResolvedValue({
      success: true,
      total_targets: 1,
      bounties: [SAMPLE],
    });

    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });

    expect(getAvailable).toHaveBeenCalledWith(20);
    expect(container.querySelector('[data-testid="bounty-board-loading"]')).toBeNull();
    expect(container.textContent).toContain('Rogue');
    expect(container.textContent).toContain('Villain');
    expect(container.textContent).toContain('137,000');
    expect(container.textContent).toContain('442');
    expect(container.querySelector('[data-testid="bounty-board-total"]')?.textContent).toMatch(/1 target/);
    expect(container.querySelector('[data-testid="bounty-board-portrait-omitted"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bounty-board-kills-omitted"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bounty-board-optional-residual"]')?.textContent)
      .toMatch(/not in the available-bounties API/i);
  });

  it('shows empty state when the board has no targets', async () => {
    getAvailable.mockResolvedValue({ success: true, bounties: [], total_targets: 0 });
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('[data-testid="bounty-board-empty"]')?.textContent)
      .toMatch(/No active bounties/i);
  });

  it('surfaces fetch errors', async () => {
    getAvailable.mockRejectedValue(new Error('board offline'));
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('[data-testid="bounty-board-error"]')?.textContent)
      .toContain('board offline');
  });

  it('surfaces LIST 403 permission error in the alert (not empty board)', async () => {
    getAvailable.mockRejectedValue(
      apiRequestError(403, 'Galactic Citizen membership required to browse bounties.'),
    );
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    const alert = container.querySelector('[data-testid="bounty-board-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('Galactic Citizen membership required');
    expect(container.querySelector('[data-testid="bounty-board-empty"]')).toBeNull();
  });

  it('surfaces LIST 429 rate-limit error in the alert (not empty board)', async () => {
    getAvailable.mockRejectedValue(apiRequestError(429));
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    const alert = container.querySelector('[data-testid="bounty-board-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toMatch(/rate limit exceeded/i);
    expect(container.querySelector('[data-testid="bounty-board-empty"]')).toBeNull();
  });

  it('formatBountyBoardLoadError falls back on TypeError network collapse (LEG-3008)', () => {
    const text = formatBountyBoardLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load bounty board/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatBountyBoardLoadError falls back on axios Network Error / Failed to fetch (LEG-3344)', () => {
    expect(formatBountyBoardLoadError(new Error('Network Error'))).toMatch(/Failed to load bounty board/i);
    expect(formatBountyBoardLoadError(new Error('Failed to fetch'))).toMatch(/Failed to load bounty board/i);
    expect(formatBountyBoardLoadError(new Error('Network Error'))).not.toBe('Network Error');
  });

  it('surfaces honest load fallback when getAvailable rejects with TypeError', async () => {
    getAvailable.mockRejectedValue(new TypeError('Failed to fetch'));
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    const alert = container.querySelector('[data-testid="bounty-board-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toMatch(/Failed to load bounty board/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });

  it('surfaces honest load fallback when getAvailable rejects with axios Network Error (LEG-3519)', async () => {
    getAvailable.mockRejectedValue(new Error('Network Error'));
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    const alert = container.querySelector('[data-testid="bounty-board-error"]');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toMatch(/Failed to load bounty board/i);
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });

  it('marks missing sector as unavailable without inventing a value', async () => {
    getAvailable.mockResolvedValue({
      success: true,
      total_targets: 1,
      bounties: [
        {
          player_id: 'p2',
          player_name: 'Ghost',
          reputation_tier: 'Outlaw',
          total_bounty: 500,
          bounty_count: 1,
          current_sector: null,
        },
      ],
    });
    await act(async () => {
      root.render(<BountyBoard />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.querySelector('[data-testid="bounty-board-sector-omitted"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bounty-board-sector"]')).toBeNull();
  });

  it('refetches when bountyEventSignal bumps (without remount)', async () => {
    getAvailable
      .mockResolvedValueOnce({
        success: true,
        total_targets: 1,
        bounties: [{ ...SAMPLE, total_bounty: 100, bounty_count: 1 }],
      })
      .mockResolvedValueOnce({
        success: true,
        total_targets: 1,
        bounties: [{ ...SAMPLE, total_bounty: 999, bounty_count: 2 }],
      });

    await act(async () => {
      root.render(<BountyBoard bountyEventSignal={0} />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.textContent).toContain('100');
    expect(getAvailable).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.render(<BountyBoard bountyEventSignal={1} />);
    });
    await act(async () => {
      await flush();
    });

    expect(getAvailable).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('999');
  });
});
