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

import RecoveryConsolePanel from '../RecoveryConsolePanel';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('RecoveryConsolePanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetStatus.mockResolvedValue({
      distress_beacon: { available: true, cooldown_until: null },
      slipdrive: { charging: false, charge_deadline: null, ready: false },
    });
    mockFireDistress.mockResolvedValue({ ok: true });
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
});
