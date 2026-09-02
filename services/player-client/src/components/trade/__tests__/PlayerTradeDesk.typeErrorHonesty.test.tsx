// @vitest-environment jsdom
/**
 * LEG-3621 Soft-ORDER — PlayerTradeDesk TypeError / Network Error densify.
 * LEG-4055 Soft-ORDER — HTTP 429 densify (invent=0).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
    deliverFuel: vi.fn(),
  },
}));

vi.mock('../../../services/api', () => ({ tradeAPI }));

import PlayerTradeDesk, { formatTradeError } from '../PlayerTradeDesk';

const ME = 'player-me';
const THEM = 'player-them';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatTradeError TypeError densify (LEG-3621)', () => {
  it('falls back on TypeError network collapse for trade refresh/open/action', () => {
    expect(formatTradeError(new TypeError('Failed to fetch'), 'trade_refresh_failed')).toBe(
      'Could not refresh the trade desk.',
    );
    expect(formatTradeError(new TypeError('Failed to fetch'), 'trade_open_failed')).toBe(
      'Could not open a trade.',
    );
    expect(formatTradeError(new TypeError('Failed to fetch'), 'trade_action_failed')).toBe(
      'That trade action failed.',
    );
    expect(formatTradeError(new TypeError('Failed to fetch'), 'trade_action_failed')).not.toMatch(
      /Failed to fetch/i,
    );
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatTradeError(new Error('Network Error'), 'trade_refresh_failed')).toBe(
      'Could not refresh the trade desk.',
    );
    expect(formatTradeError(new Error('Network Error'), 'trade_open_failed')).toBe(
      'Could not open a trade.',
    );
    expect(formatTradeError(new Error('Failed to fetch'), 'trade_action_failed')).toBe(
      'That trade action failed.',
    );
    expect(formatTradeError(new Error('Network Error'), 'trade_action_failed')).not.toMatch(
      /Network Error/i,
    );
  });

  it('uses 403 trade-scope fallback when detail is a bare API Error blob', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatTradeError(err, 'trade_open_failed')).toBe(
      'Access denied — you cannot trade right now.',
    );
    expect(formatTradeError(err, 'trade_open_failed')).not.toMatch(/API Error: 403/i);
  });

  it('preserves gameserver detail on 403 when present', () => {
    const err = Object.assign(new Error('not_co_located'), { status: 403 });
    expect(formatTradeError(err, 'trade_open_failed')).toBe('You must be in the same location to trade.');
  });

  it('maps 429 to player-safe rate-limit copy without raw transport leakage (LEG-4055)', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatTradeError(err, 'trade_open_failed')).toBe(
      'Trade rate limit exceeded — wait a moment and try again.',
    );
    expect(formatTradeError(err, 'trade_open_failed')).toMatch(/rate limit/i);
    expect(formatTradeError(err, 'trade_open_failed')).not.toMatch(/\b429\b/);
    expect(formatTradeError(err, 'trade_open_failed')).not.toMatch(/API Error/i);
    expect(formatTradeError(err, 'trade_open_failed')).not.toMatch(/Network Error/i);
    const denied = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatTradeError(denied, 'trade_open_failed')).toBe(
      'Access denied — you cannot trade right now.',
    );
    expect(formatTradeError(denied, 'trade_open_failed')).not.toMatch(/TypeError/i);
  });
});

describe('PlayerTradeDesk load/initiate TypeError honesty (LEG-3621)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onClose: () => void;

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
    document.body.querySelectorAll('.p2p-trade-desk-backdrop').forEach((el) => el.remove());
  });

  it('load getOpen TypeError surfaces honest fallback without Failed to fetch / TypeError in DOM', async () => {
    tradeAPI.getOpen.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} />);
    });
    await act(async () => {
      await flush();
    });

    const alert = document.body.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/Could not open a trade/i);
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
  });

  it('load getOpen Network Error surfaces honest fallback without Network Error in DOM', async () => {
    tradeAPI.getOpen.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<PlayerTradeDesk myPlayerId={ME} onClose={onClose} />);
    });
    await act(async () => {
      await flush();
    });

    const alert = document.body.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/Could not open a trade/i);
    expect(document.body.textContent).not.toMatch(/Network Error/i);
  });

  it('initiate TypeError surfaces honest fallback without Failed to fetch / TypeError in DOM', async () => {
    tradeAPI.getOpen.mockResolvedValue({ session: null });
    tradeAPI.initiate.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(
        <PlayerTradeDesk targetPlayerId={THEM} myPlayerId={ME} onClose={onClose} />,
      );
    });
    await act(async () => {
      await flush();
    });

    const alert = document.body.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/Could not open a trade/i);
    expect(document.body.textContent).not.toMatch(/Failed to fetch/i);
    expect(document.body.textContent).not.toMatch(/TypeError/i);
  });

  it('initiate Network Error surfaces honest fallback without Network Error in DOM', async () => {
    tradeAPI.getOpen.mockResolvedValue({ session: null });
    tradeAPI.initiate.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(
        <PlayerTradeDesk targetPlayerId={THEM} myPlayerId={ME} onClose={onClose} />,
      );
    });
    await act(async () => {
      await flush();
    });

    const alert = document.body.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/Could not open a trade/i);
    expect(document.body.textContent).not.toMatch(/Network Error/i);
  });
});
