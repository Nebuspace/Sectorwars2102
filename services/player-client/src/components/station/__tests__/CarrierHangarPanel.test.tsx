// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getStatus, accept, undock } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  accept: vi.fn(),
  undock: vi.fn(),
  cancel: vi.fn(),
  requestDock: vi.fn(),
  disembark: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  hangarAPI: {
    getStatus: (...a: unknown[]) => getStatus(...a),
    accept: (...a: unknown[]) => accept(...a),
    undock: (...a: unknown[]) => undock(...a),
    cancel: vi.fn(),
    requestDock: vi.fn(),
    disembark: vi.fn(),
  },
}));

vi.mock('../../tactical/contactClassification', () => ({
  useSectorContacts: () => [],
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ refreshPlayerState: vi.fn().mockResolvedValue(undefined) }),
}));

import CarrierHangarPanel, { formatHangarActionError } from '../CarrierHangarPanel';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('CarrierHangarPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getStatus.mockReset();
    accept.mockReset().mockResolvedValue({ status: 'DOCKED' });
    undock.mockReset().mockResolvedValue({ undocked: true });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it('renders nothing when idle with no carriers', async () => {
    getStatus.mockResolvedValue({
      hangared_on: null,
      pending_outgoing: null,
      owned_carrier: null,
    });
    await act(async () => {
      root.render(<CarrierHangarPanel />);
    });
    await flush();
    expect(container.querySelector('[data-testid="carrier-hangar-panel"]')).toBeNull();
  });

  it('Accepts a pending dock request as captain', async () => {
    getStatus
      .mockResolvedValueOnce({
        hangared_on: null,
        pending_outgoing: null,
        owned_carrier: {
          carrier_id: 'c1',
          capacity_units: 8,
          used_units: 0,
          docked: [{ ship_id: 's1', request_state: 'PENDING', size_units: 2 }],
        },
      })
      .mockResolvedValue({
        hangared_on: null,
        pending_outgoing: null,
        owned_carrier: {
          carrier_id: 'c1',
          capacity_units: 8,
          used_units: 2,
          docked: [{ ship_id: 's1', request_state: 'DOCKED', size_units: 2 }],
        },
      });

    await act(async () => {
      root.render(<CarrierHangarPanel />);
    });
    await flush();

    const btn = container.querySelector(
      '[data-testid="carrier-hangar-accept-s1"]'
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    await flush();
    expect(accept).toHaveBeenCalledWith('c1', 's1');
  });

  it('surfaces accept 400 server detail in feedback', async () => {
    getStatus.mockResolvedValue({
      hangared_on: null, pending_outgoing: null,
      owned_carrier: { carrier_id: 'c1', capacity_units: 8, used_units: 0, docked: [{ ship_id: 's1', request_state: 'PENDING', size_units: 2 }] },
    });
    accept.mockRejectedValue(Object.assign(new Error('Hangar capacity exceeded'), { status: 400 }));
    await act(async () => { root.render(<CarrierHangarPanel />); });
    await flush();
    const btn = container.querySelector('[data-testid="carrier-hangar-accept-s1"]') as HTMLButtonElement;
    await act(async () => { btn.click(); });
    await flush();
    expect(container.querySelector('[data-testid="carrier-hangar-feedback"]')?.textContent).toBe('Hangar capacity exceeded');
  });

  it('formatHangarActionError falls back when detail absent', () => {
    expect(formatHangarActionError(new Error('API Error: 500'))).toBe('Hangar action failed');
  });
});
