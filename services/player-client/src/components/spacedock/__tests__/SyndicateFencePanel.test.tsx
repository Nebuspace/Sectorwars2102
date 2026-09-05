// @vitest-environment jsdom
/**
 * SyndicateFencePanel — LEG-4112 Shadow Syndicate cargo fencing.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getFence = vi.fn();
const fenceCargo = vi.fn();
const refreshPlayerState = vi.fn();
const updatePlayerCredits = vi.fn();

let mockPlayerState: { id?: string; credits?: number; is_docked?: boolean } | null = {
  id: 'player-1',
  credits: 1000,
  is_docked: true,
};

let mockCurrentShip: {
  id: string;
  cargo: {
    used: number;
    capacity: number;
    contents: Record<string, number>;
    flagged_origin: Record<string, number>;
  };
} | null = {
  id: 'ship-1',
  cargo: {
    used: 10,
    capacity: 50,
    contents: { ore: 10 },
    flagged_origin: { ore: 4 },
  },
};

vi.mock('../../../services/api', () => ({
  syndicateFenceAPI: {
    getFence: (...args: unknown[]) => getFence(...args),
    fenceCargo: (...args: unknown[]) => fenceCargo(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: mockCurrentShip,
    playerState: mockPlayerState,
    refreshPlayerState,
    updatePlayerCredits,
  }),
}));

vi.mock('../../../utils/formatters', () => ({
  formatCredits: (n: number) => `${n} cr`,
}));

import SyndicateFencePanel, {
  formatSyndicateFenceError,
  probeSyndicateFence,
} from '../SyndicateFencePanel';
import { syndicateFenceAPI } from '../../../services/api';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const STATION_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatSyndicateFenceError (LEG-4112)', () => {
  const fallback = 'Could not fence that cargo — try again.';

  it('densifies TypeError network collapse', () => {
    expect(formatSyndicateFenceError(new TypeError('Failed to fetch'), fallback)).toBe(
      fallback,
    );
  });

  it('surfaces 403 permission path without raw status codes', () => {
    expect(formatSyndicateFenceError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatSyndicateFenceError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
  });

  it('surfaces 429 rate-limit path without raw status codes', () => {
    expect(formatSyndicateFenceError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatSyndicateFenceError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatSyndicateFenceError(apiRequestError(429), fallback)).not.toMatch(/TypeError/i);
  });
});

describe('probeSyndicateFence', () => {
  beforeEach(() => {
    getFence.mockReset();
  });

  it('returns info on GET success', async () => {
    getFence.mockResolvedValue({
      station_id: STATION_ID,
      has_syndicate_fence: true,
      services: ['cargo_fencing'],
      payout_percent: 70,
    });
    await expect(probeSyndicateFence(STATION_ID)).resolves.toMatchObject({
      has_syndicate_fence: true,
      payout_percent: 70,
    });
  });

  it('returns null on GET failure (404 gate)', async () => {
    getFence.mockRejectedValue(apiRequestError(404, 'Not found'));
    await expect(probeSyndicateFence(STATION_ID)).resolves.toBeNull();
  });
});

describe('SyndicateFencePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getFence.mockReset();
    fenceCargo.mockReset();
    refreshPlayerState.mockReset();
    updatePlayerCredits.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    mockPlayerState = { id: 'player-1', credits: 1000, is_docked: true };
    mockCurrentShip = {
      id: 'ship-1',
      cargo: {
        used: 10,
        capacity: 50,
        contents: { ore: 10 },
        flagged_origin: { ore: 4 },
      },
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('lists flagged_origin cargo and fences successfully with payout/credits', async () => {
    fenceCargo.mockResolvedValue({
      success: true,
      reason: 'ok',
      commodity: 'ore',
      quantity: 4,
      market_value: 400,
      payout: 280,
      payout_percent: 70,
      credits: 1280,
    });
    const onCreditsSet = vi.fn();

    await act(async () => {
      root.render(
        <SyndicateFencePanel
          stationId={STATION_ID}
          stationName="Shadow Port"
          fenceInfo={{
            station_id: STATION_ID,
            has_syndicate_fence: true,
            services: ['cargo_fencing'],
            payout_percent: 70,
          }}
          credits={1000}
          onCreditsSet={onCreditsSet}
        />,
      );
      await flush();
    });

    expect(container.querySelector('[data-testid="syndicate-fence-list"]')).toBeTruthy();
    expect(container.textContent).toMatch(/Ore/i);

    await act(async () => {
      const cta = container.querySelector(
        '[data-testid="syndicate-fence-cta-ore"]',
      ) as HTMLButtonElement;
      cta.click();
      await flush();
    });

    expect(fenceCargo).toHaveBeenCalledWith({
      station_id: STATION_ID,
      commodity: 'ore',
      quantity: 4,
    });
    expect(onCreditsSet).toHaveBeenCalledWith(1280);
    expect(container.querySelector('[data-testid="syndicate-fence-message"]')?.textContent).toMatch(
      /280 cr/i,
    );
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/\b429\b/);
  });

  it('shows human 403 copy without raw status code', async () => {
    fenceCargo.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(
        <SyndicateFencePanel
          stationId={STATION_ID}
          fenceInfo={{
            station_id: STATION_ID,
            has_syndicate_fence: true,
            services: ['cargo_fencing'],
            payout_percent: 70,
          }}
        />,
      );
      await flush();
    });

    await act(async () => {
      (
        container.querySelector(
          '[data-testid="syndicate-fence-cta-ore"]',
        ) as HTMLButtonElement
      ).click();
      await flush();
    });

    const text = container.querySelector('[data-testid="syndicate-fence-message"]')?.textContent ?? '';
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('shows human 429 copy without raw status code', async () => {
    fenceCargo.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(
        <SyndicateFencePanel
          stationId={STATION_ID}
          fenceInfo={{
            station_id: STATION_ID,
            has_syndicate_fence: true,
            services: ['cargo_fencing'],
            payout_percent: 70,
          }}
        />,
      );
      await flush();
    });

    await act(async () => {
      (
        container.querySelector(
          '[data-testid="syndicate-fence-cta-ore"]',
        ) as HTMLButtonElement
      ).click();
      await flush();
    });

    const text = container.querySelector('[data-testid="syndicate-fence-message"]')?.textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
  });
});

describe('syndicateFenceAPI surface (types smoke)', () => {
  it('exports tip route helpers', () => {
    expect(typeof syndicateFenceAPI.getFence).toBe('function');
    expect(typeof syndicateFenceAPI.fenceCargo).toBe('function');
  });
});
