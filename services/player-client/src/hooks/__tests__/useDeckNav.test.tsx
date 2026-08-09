// @vitest-environment jsdom
/**
 * useDeckNav (useTacticalPageRequest) — the React binding half of
 * services/deckNavBus.ts. deckNavBus.test.ts already covers the plain
 * pub/sub module's own logic (latching, monotonic ids, reset); this file
 * covers the hook's OWN binding behavior: the initial-mount snapshot read,
 * the late-arrival re-check inside the effect, subscribing for future
 * updates, and unsubscribing on unmount. Mirrors useResourceCatalog.test
 * .tsx's mock-the-service + render-a-Probe-component convention.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getLatestTacticalPageRequest = vi.fn();
const requestTacticalPage = vi.fn();
const subscribeTacticalPageRequest = vi.fn();

vi.mock('../../services/deckNavBus', () => ({
  getLatestTacticalPageRequest: (...args: unknown[]) => getLatestTacticalPageRequest(...args),
  requestTacticalPage: (...args: unknown[]) => requestTacticalPage(...args),
  subscribeTacticalPageRequest: (...args: unknown[]) => subscribeTacticalPageRequest(...args),
}));

import { useTacticalPageRequest } from '../useDeckNav';

const Probe: React.FC = () => {
  const request = useTacticalPageRequest();
  return (
    <div
      data-testid="probe"
      data-page={request?.page ?? ''}
      data-request-id={request ? String(request.requestId) : ''}
    />
  );
};

function readProbe(container: HTMLElement) {
  const el = container.querySelector('[data-testid="probe"]') as HTMLElement;
  return { page: el.dataset.page, requestId: el.dataset.requestId };
}

describe('useTacticalPageRequest', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let subscriber: ((request: { page: string; requestId: number }) => void) | null = null;
  let unsubscribeCalls = 0;

  beforeEach(() => {
    vi.clearAllMocks();
    subscriber = null;
    unsubscribeCalls = 0;
    getLatestTacticalPageRequest.mockReturnValue(null);
    subscribeTacticalPageRequest.mockImplementation((fn: typeof subscriber) => {
      subscriber = fn;
      return () => {
        unsubscribeCalls += 1;
        subscriber = null;
      };
    });
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

  it('starts null when nothing has ever fired', async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    expect(readProbe(container)).toEqual({ page: '', requestId: '' });
    expect(subscribeTacticalPageRequest).toHaveBeenCalledTimes(1);
  });

  it('picks up a pending request left over from before mount', async () => {
    getLatestTacticalPageRequest.mockReturnValue({ page: 'threat', requestId: 3 });
    await act(async () => {
      root.render(<Probe />);
    });
    expect(readProbe(container)).toEqual({ page: 'threat', requestId: '3' });
  });

  it('updates when the bus notifies a fresh request', async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    expect(readProbe(container)).toEqual({ page: '', requestId: '' });

    await act(async () => {
      subscriber?.({ page: 'target', requestId: 1 });
    });
    expect(readProbe(container)).toEqual({ page: 'target', requestId: '1' });
  });

  it('re-fires on a repeat request for the same page (monotonic requestId)', async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    await act(async () => {
      subscriber?.({ page: 'target', requestId: 1 });
    });
    await act(async () => {
      subscriber?.({ page: 'target', requestId: 2 });
    });
    expect(readProbe(container)).toEqual({ page: 'target', requestId: '2' });
  });

  it('unsubscribes on unmount', async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    expect(unsubscribeCalls).toBe(0);
    await act(async () => {
      root.unmount();
    });
    expect(unsubscribeCalls).toBe(1);
  });

  it('re-exports requestTacticalPage from the underlying bus module', async () => {
    const mod = await import('../useDeckNav');
    mod.requestTacticalPage('threat');
    expect(requestTacticalPage).toHaveBeenCalledWith('threat');
  });
});
