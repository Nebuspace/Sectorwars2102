// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let wsState: {
  lastLimpetSignal: {
    tracked_player_id: string | null;
    tracked_ship_id: string | null;
    sector_id: number | null;
  } | null;
  limpetSignalEventSignal: number;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsState,
}));

import LimpetTrackerReadout from '../LimpetTrackerReadout';

describe('LimpetTrackerReadout', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    wsState = { lastLimpetSignal: null, limpetSignalEventSignal: 0 };
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

  it('empty until a signal is stashed', async () => {
    await act(async () => {
      root.render(<LimpetTrackerReadout />);
    });
    expect(container.textContent).toContain('No active limpet signal');
  });

  it('renders sector_id and ship from lastLimpetSignal', async () => {
    wsState = {
      limpetSignalEventSignal: 2,
      lastLimpetSignal: {
        tracked_player_id: 'p9',
        tracked_ship_id: 'ship-aa',
        sector_id: 55,
      },
    };
    await act(async () => {
      root.render(<LimpetTrackerReadout />);
    });
    const fix = container.querySelector('[data-testid="limpet-tracker-fix"]');
    expect(fix?.textContent).toContain('Sector 55');
    expect(fix?.textContent).toContain('ship-aa');
  });
});
