// @vitest-environment jsdom
/**
 * LEG-3738 Soft-ORDER — RecoveryConsolePanel action TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetStatus, mockFireDistress, mockBegin } = vi.hoisted(() => ({
  mockGetStatus: vi.fn(),
  mockFireDistress: vi.fn(),
  mockBegin: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  recoveryAPI: {
    getStatus: mockGetStatus,
    fireDistressBeacon: mockFireDistress,
    beginSlipdrive: mockBegin,
    completeSlipdrive: vi.fn(),
    escapePod: vi.fn(),
  },
}));

const mockRefresh = vi.fn(() => Promise.resolve());
const mockLoadShips = vi.fn(() => Promise.resolve());

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    refreshPlayerState: mockRefresh,
    loadShips: mockLoadShips,
  }),
}));

import RecoveryConsolePanel, { formatRecoveryActionError } from '../RecoveryConsolePanel';

const FALLBACK = 'Recovery action failed';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('RecoveryConsolePanel TypeError densify (LEG-3738)', () => {
  it('formatRecoveryActionError falls back on TypeError network collapse', () => {
    const text = formatRecoveryActionError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatRecoveryActionError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatRecoveryActionError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatRecoveryActionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves gameserver detail from structured axios payload', () => {
    const err = {
      data: { detail: 'Slipdrive requires a warp jumper module' },
    };
    expect(formatRecoveryActionError(err)).toBe('Slipdrive requires a warp jumper module');
  });
});

describe('RecoveryConsolePanel action transport collapse densify (LEG-3738)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetStatus.mockResolvedValue({
      distress_beacon: { available: true, cooldown_until: null },
      slipdrive: { charging: false, charge_deadline: null, ready: false },
    });
    mockFireDistress.mockRejectedValue(new Error('Network Error'));
    mockBegin.mockResolvedValue({ ok: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('distress beacon Network Error surfaces fallback without raw transport text', async () => {
    await act(async () => {
      root.render(<RecoveryConsolePanel />);
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="recovery-console-open"]') as HTMLButtonElement).click();
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="recovery-distress-fire"]') as HTMLButtonElement).click();
      await flush();
      await flush();
    });

    const feedback = container.querySelector('.recovery-console-feedback');
    expect(feedback?.textContent).toBe(FALLBACK);
    expect(feedback?.textContent).not.toMatch(/Network Error/i);
  });
});

describe('formatRecoveryActionError 403/429 densify (LEG-4084)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatRecoveryActionError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatRecoveryActionError(apiRequestError(403, 'recovery_denied'))).toBe(
      'recovery_denied',
    );
    expect(formatRecoveryActionError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatRecoveryActionError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatRecoveryActionError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
