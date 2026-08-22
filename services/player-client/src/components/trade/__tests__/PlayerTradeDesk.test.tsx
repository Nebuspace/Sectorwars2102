// @vitest-environment jsdom
/**
 * PlayerTradeDesk — money-path coverage (WO-TESTCOV-PLAYER-TRADE-DESK + LEG-1478).
 * Exercises initiate / accept / open staging / commodity+ship offers / error prose.
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
const SHIP_A = 'ship-cargo-a';
const SHIP_B = 'ship-offer-b';

const TEST_SHIPS = [
  { id: SHIP_A, name: 'Hauler', cargo: { ore: 40, fuel: 10 } },
  { id: SHIP_B, name: 'Scout', cargo: {} },
];

function openSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 'sess-1',
    initiator_id: ME,
    target_id: THEM,
    status: 'OPEN',
    version: 1,
    initiator_offer: { credits: 0, commodities: {}, ships: [] },
    target_offer: { credits: 0, commodities: {}, ships: [] },
    initiator_confirmed_version: null,
    target_confirmed_version: null,
    ...overrides,
  };
}

describe('PlayerTradeDesk', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onClose: () => void;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    onClose = vi.fn<() => void>();
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
        <PlayerTradeDesk targetPlayerId={THEM} myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />,
      );
    });
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
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />);
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
        initiator_offer: { credits: 500, commodities: {}, ships: [] },
      }),
    });

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const input = document.body.querySelector(
      '[data-testid="credits-offer"]',
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

    const stage = document.body.querySelector(
      '[data-testid="stage-offer"]',
    ) as HTMLButtonElement;
    await act(async () => {
      stage.click();
      await Promise.resolve();
    });

    expect(tradeAPI.offer).toHaveBeenCalledWith('sess-1', {
      credits: 500,
      commodities: {},
      ship_id: null,
      ships: [],
    });
    expect(document.body.querySelector('.p2p-trade-desk')?.textContent).toContain(
      'Offer staged',
    );
  });

  it('stages commodities with ship_id via existing tradeAPI.offer', async () => {
    tradeAPI.getOpen.mockResolvedValue({ session: openSession() });
    tradeAPI.offer.mockResolvedValue({
      session: openSession({
        version: 2,
        initiator_offer: {
          credits: 0,
          commodities: { ore: 5 },
          ship_id: SHIP_A,
          ships: [],
        },
      }),
    });

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const cargoSelect = document.body.querySelector(
      '[data-testid="cargo-ship-select"]',
    ) as HTMLSelectElement;
    await act(async () => {
      cargoSelect.value = SHIP_A;
      cargoSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const commoditySelect = document.body.querySelector(
      '[data-testid="commodity-key"]',
    ) as HTMLSelectElement;
    await act(async () => {
      commoditySelect.value = 'ore';
      commoditySelect.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const qty = document.body.querySelector(
      '[data-testid="commodity-qty"]',
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(qty, '5');
      qty.dispatchEvent(new Event('input', { bubbles: true }));
      qty.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await act(async () => {
      (document.body.querySelector('[data-testid="add-commodity"]') as HTMLButtonElement).click();
    });

    expect(document.body.querySelector('[data-testid="commodity-draft"]')?.textContent).toContain(
      'ore × 5',
    );

    await act(async () => {
      (document.body.querySelector('[data-testid="stage-offer"]') as HTMLButtonElement).click();
      await Promise.resolve();
    });

    expect(tradeAPI.offer).toHaveBeenCalledWith('sess-1', {
      credits: 0,
      commodities: { ore: 5 },
      ship_id: SHIP_A,
      ships: [],
    });
  });

  it('stages ships[] offer and renders ship lines in summaries', async () => {
    tradeAPI.getOpen.mockResolvedValue({ session: openSession() });
    tradeAPI.offer.mockResolvedValue({
      session: openSession({
        version: 2,
        initiator_offer: {
          credits: 0,
          commodities: {},
          ships: [SHIP_B],
        },
        target_offer: {
          credits: 100,
          commodities: { fuel: 2 },
          ships: [],
        },
      }),
    });

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const scoutBox = document.body.querySelector(
      'input[aria-label="Offer ship Scout"]',
    ) as HTMLInputElement;
    await act(async () => {
      scoutBox.click();
    });

    await act(async () => {
      (document.body.querySelector('[data-testid="stage-offer"]') as HTMLButtonElement).click();
      await Promise.resolve();
    });

    expect(tradeAPI.offer).toHaveBeenCalledWith('sess-1', {
      credits: 0,
      commodities: {},
      ship_id: null,
      ships: [SHIP_B],
    });

    const desk = document.body.querySelector('.p2p-trade-desk');
    expect(desk?.textContent).toContain('Ship: Scout');
    expect(desk?.textContent).toContain('fuel × 2');
  });

  it('maps known server reason codes to cockpit prose', async () => {
    tradeAPI.getOpen.mockRejectedValue(new Error('not_co_located'));

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />);
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
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} ships={TEST_SHIPS} />);
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
