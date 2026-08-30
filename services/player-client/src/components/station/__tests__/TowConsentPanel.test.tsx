// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getStatus, accept, cancel } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  accept: vi.fn(),
  cancel: vi.fn(),
  request: vi.fn(),
  detach: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  towAPI: {
    getStatus: (...a: unknown[]) => getStatus(...a),
    accept: (...a: unknown[]) => accept(...a),
    cancel: (...a: unknown[]) => cancel(...a),
    request: vi.fn(),
    detach: vi.fn(),
  },
}));

vi.mock('../../tactical/contactClassification', () => ({
  useSectorContacts: () => [],
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: { id: 'ship-hauler', type: 'CARGO_HAULER' },
    refreshPlayerState: vi.fn(),
    updatePlayerCredits: vi.fn(),
  }),
}));

vi.mock('../TractorBeamInstallCta', () => ({
  default: () => <div data-testid="tractor-beam-cta-stub" />,
}));

import TowConsentPanel, { formatTowActionError } from '../TowConsentPanel';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('TowConsentPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getStatus.mockReset();
    accept.mockReset().mockResolvedValue({ status: 'LOCKED' });
    cancel.mockReset().mockResolvedValue({ cancelled: true });
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

  it('renders nothing when status is idle and no contacts', async () => {
    getStatus.mockResolvedValue({
      towing: null,
      being_towed_by: null,
      pending_outgoing: null,
      pending_incoming: null,
    });
    await act(async () => {
      root.render(<TowConsentPanel />);
    });
    await flush();
    expect(container.querySelector('[data-testid="tow-consent-panel"]')).toBeNull();
    expect(container.querySelector('[data-testid="tow-consent-rail"]')).toBeNull();
  });

  it('Accepts an incoming pending tow', async () => {
    getStatus
      .mockResolvedValueOnce({
        towing: null,
        being_towed_by: null,
        pending_outgoing: null,
        pending_incoming: {
          hauler_id: 'hauler-1',
          towed_ship_id: 'me',
          surcharge_per_move: 2,
          request_state: 'PENDING',
        },
      })
      .mockResolvedValue({
        towing: null,
        being_towed_by: { hauler_id: 'hauler-1', surcharge_per_move: 2 },
        pending_outgoing: null,
        pending_incoming: null,
      });

    await act(async () => {
      root.render(<TowConsentPanel />);
    });
    await flush();

    expect(container.querySelector('[data-testid="tow-consent-incoming"]')).not.toBeNull();
    const btn = container.querySelector(
      '[data-testid="tow-consent-accept"]'
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    await flush();

    expect(accept).toHaveBeenCalledWith('hauler-1');
  });

  it('surfaces accept 400 server detail in feedback', async () => {
    getStatus.mockResolvedValue({
      towing: null, being_towed_by: null, pending_outgoing: null,
      pending_incoming: { hauler_id: 'hauler-1', towed_ship_id: 'me', surcharge_per_move: 2, request_state: 'PENDING' },
    });
    accept.mockRejectedValue(Object.assign(new Error('Tow request expired'), { status: 400 }));
    await act(async () => { root.render(<TowConsentPanel />); });
    await flush();
    const btn = container.querySelector('[data-testid="tow-consent-accept"]') as HTMLButtonElement;
    await act(async () => { btn.click(); });
    await flush();
    expect(container.querySelector('[data-testid="tow-consent-feedback"]')?.textContent).toBe('Tow request expired');
  });

  it('formatTowActionError falls back when detail absent', () => {
    expect(formatTowActionError(new Error('API Error: 500'))).toBe('Tow action failed');
  });
  it('formatTowActionError falls back on TypeError network collapse (LEG-3003)', () => {
    const text = formatTowActionError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Tow action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

});
