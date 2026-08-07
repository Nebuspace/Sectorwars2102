// @vitest-environment jsdom
/**
 * PlayerTradeDesk — money-path coverage (WO-TESTCOV-PLAYER-TRADE-DESK).
 * Exercises initiate / accept / open staging / error prose against mocked tradeAPI.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { tradeAPI } = vi.hoisted(() => ({
  tradeAPI: {
    getOpen: vi.fn(),
    initiate: vi.fn(),
    get: vi.fn(),
    accept: vi.fn(),
    decline: vi.fn(),
    offer: vi.fn(),
    confirm: vi.fn(),
    cancel: vi.fn(),
  },
}));

vi.mock('../../../services/api', () => ({ tradeAPI }));

import PlayerTradeDesk from '../PlayerTradeDesk';

const ME = 'player-me';
const THEM = 'player-them';

function openSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 'sess-1',
    initiator_id: ME,
    target_id: THEM,
    status: 'OPEN',
    version: 1,
    initiator_offer: { credits: 0 },
    target_offer: { credits: 0 },
    initiator_confirmed_version: null,
    target_confirmed_version: null,
    ...overrides,
  };
}

describe('PlayerTradeDesk', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onClose: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    onClose = vi.fn();
    vi.clearAllMocks();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    // portals mount on document.body
    document.body.querySelectorAll('.p2p-trade-desk-backdrop').forEach((el) => el.remove());
  });

  it('initiates a trade when no open session and targetPlayerId is set', async () => {
    tradeAPI.getOpen.mockResolvedValue({ session: null });
    tradeAPI.initiate.mockResolvedValue({
      session: openSession({ status: 'PENDING_ACCEPT' }),
    });

    await act(async () => {
      root.render(
        <PlayerTradeDesk targetPlayerId={THEM} myPlayerId={ME} onClose={onClose} />,
      );
    });
    // flush effect
    await act(async () => {
      await Promise.resolve();
    });

    expect(tradeAPI.initiate).toHaveBeenCalledWith(THEM);
    const desk = document.body.querySelector('.p2p-trade-desk');
    expect(desk?.textContent).toContain('Trade invite sent');
    expect(desk?.textContent).toContain('Awaiting accept');
    expect(desk?.textContent).toContain('Waiting for the other captain');
  });

  it('shows Accept/Decline for the target on PENDING_ACCEPT', async () => {
    tradeAPI.getOpen.mockResolvedValue({
      session: openSession({
        status: 'PENDING_ACCEPT',
        initiator_id: THEM,
        target_id: ME,
      }),
    });
    tradeAPI.accept.mockResolvedValue({
      session: openSession({
        status: 'OPEN',
        initiator_id: THEM,
        target_id: ME,
      }),
    });

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const accept = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent === 'Accept',
    ) as HTMLButtonElement;
    expect(accept).toBeDefined();

    await act(async () => {
      accept.click();
      await Promise.resolve();
    });

    expect(tradeAPI.accept).toHaveBeenCalledWith('sess-1');
    expect(document.body.querySelector('.p2p-trade-desk')?.textContent).toContain('Trade open');
  });

  it('stages a credits offer while OPEN', async () => {
    tradeAPI.getOpen.mockResolvedValue({ session: openSession() });
    tradeAPI.offer.mockResolvedValue({
      session: openSession({
        version: 2,
        initiator_offer: { credits: 500 },
      }),
    });

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const input = document.body.querySelector(
      'input[type="number"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(input, '500');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const stage = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent === 'Stage offer',
    ) as HTMLButtonElement;
    await act(async () => {
      stage.click();
      await Promise.resolve();
    });

    expect(tradeAPI.offer).toHaveBeenCalledWith('sess-1', { credits: 500 });
    expect(document.body.querySelector('.p2p-trade-desk')?.textContent).toContain(
      'Offer staged',
    );
  });

  it('maps known server reason codes to cockpit prose', async () => {
    tradeAPI.getOpen.mockRejectedValue(new Error('not_co_located'));

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const alert = document.body.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe('You must be in the same location to trade.');
  });

  it('closes after a terminal SETTLED session', async () => {
    tradeAPI.getOpen.mockResolvedValue({
      session: openSession({ status: 'SETTLED' }),
    });

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(document.body.querySelector('.p2p-trade-desk')?.textContent).toContain(
      'Deal complete',
    );
    const close = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent === 'Close',
    ) as HTMLButtonElement;
    await act(async () => {
      close.click();
    });
    expect(onClose).toHaveBeenCalled();
  });
});
