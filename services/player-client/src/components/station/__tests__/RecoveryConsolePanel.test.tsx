// @vitest-environment jsdom
/**
 * RecoveryConsolePanel — WO-WIRE-RECOVERY-CONSOLE.
 * Pins open → distress fire wiring against recoveryAPI.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGetStatus, mockFireDistress, mockBegin, mockComplete, mockEscape } = vi.hoisted(
  () => ({
    mockGetStatus: vi.fn(),
    mockFireDistress: vi.fn(),
    mockBegin: vi.fn(),
    mockComplete: vi.fn(),
    mockEscape: vi.fn(),
  }),
);

vi.mock('../../../services/api', () => ({
  recoveryAPI: {
    getStatus: mockGetStatus,
    fireDistressBeacon: mockFireDistress,
    beginSlipdrive: mockBegin,
    completeSlipdrive: mockComplete,
    escapePod: mockEscape,
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

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('formatRecoveryActionError', () => {
  it('preserves nested structured distress detail from data.detail.detail', () => {
    const err = new Error('API Error: 400');
    (err as { status?: number; data?: unknown }).status = 400;
    (err as { data?: unknown }).data = {
      detail: {
        detail: 'You cannot fire a distress beacon while docked -- launch first',
        cooldown_until: null,
      },
    };
    expect(formatRecoveryActionError(err)).toBe(
      'You cannot fire a distress beacon while docked -- launch first',
    );
  });

  it('preserves plain string 400 detail (slipdrive / escape-pod)', () => {
    const err = new Error('Slipdrive requires a warp jumper module');
    (err as { status?: number }).status = 400;
    expect(formatRecoveryActionError(err)).toBe('Slipdrive requires a warp jumper module');
  });

  it('falls back when only bare API Error status is present', () => {
    expect(formatRecoveryActionError(new Error('API Error: 500'))).toBe('Recovery action failed');
  });
});

describe('RecoveryConsolePanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetStatus.mockResolvedValue({
      distress_beacon: { available: true, cooldown_until: null },
      slipdrive: { charging: false, charge_deadline: null, ready: false },
    });
    mockFireDistress.mockResolvedValue({ ok: true });
    mockBegin.mockResolvedValue({ ok: true });
    mockRefresh.mockClear();
    mockLoadShips.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  it('shows a Recovery rail when collapsed', async () => {
    await act(async () => {
      root.render(<RecoveryConsolePanel />);
      await flush();
    });
    expect(container.querySelector('[data-testid="recovery-console-rail"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="recovery-console-panel"]')).toBeNull();
  });

  it('opens the desk and fires the distress beacon', async () => {
    await act(async () => {
      root.render(<RecoveryConsolePanel />);
      await flush();
    });

    await act(async () => {
      (container.querySelector('[data-testid="recovery-console-open"]') as HTMLButtonElement).click();
      await flush();
    });

    expect(container.querySelector('[data-testid="recovery-console-panel"]')).toBeTruthy();

    await act(async () => {
      (container.querySelector('[data-testid="recovery-distress-fire"]') as HTMLButtonElement).click();
      await flush();
      await flush();
    });

    expect(mockFireDistress).toHaveBeenCalledTimes(1);
    expect(mockRefresh).toHaveBeenCalled();
    expect(mockLoadShips).toHaveBeenCalled();
  });

  it('surfaces nested structured distress refusal in feedback', async () => {
    const err = new Error('API Error: 400');
    (err as { status?: number; data?: unknown }).status = 400;
    (err as { data?: unknown }).data = {
      detail: {
        detail: 'You cannot fire a distress beacon while docked -- launch first',
      },
    };
    mockFireDistress.mockRejectedValueOnce(err);

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
    expect(feedback?.textContent).toBe(
      'You cannot fire a distress beacon while docked -- launch first',
    );
  });

  it('surfaces plain 400 slipdrive refusal in feedback', async () => {
    mockBegin.mockRejectedValueOnce(new Error('Slipdrive requires a warp jumper module'));

    await act(async () => {
      root.render(<RecoveryConsolePanel />);
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="recovery-console-open"]') as HTMLButtonElement).click();
      await flush();
    });
    await act(async () => {
      (container.querySelector('[data-testid="recovery-slipdrive-begin"]') as HTMLButtonElement).click();
      await flush();
      await flush();
    });

    const feedback = container.querySelector('.recovery-console-feedback');
    expect(feedback?.textContent).toBe('Slipdrive requires a warp jumper module');
  });
});
