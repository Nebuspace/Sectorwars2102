// @vitest-environment jsdom
/**
 * ServiceRecordTab — RankDisplay + RankProgress + MedalShowcase + CombatHistoryPanel
 * + BountyBoard + BountyPlaceCancel.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../ranking/RankDisplay', () => ({
  default: () => <div data-testid="rank-display" />,
}));
vi.mock('../../ranking/RankProgress', () => ({
  default: () => <div data-testid="rank-progress" />,
}));
vi.mock('../../ranking/MedalShowcase', () => ({
  default: () => <div data-testid="medal-showcase" />,
}));
vi.mock('../../combat/CombatHistoryPanel', () => ({
  CombatHistoryPanel: () => <div data-testid="combat-history-panel" />,
}));
vi.mock('../../ranking/BountyBoard', () => ({
  default: () => <div data-testid="bounty-board" />,
}));
vi.mock('../../ranking/BountyPlaceCancel', () => ({
  default: () => <div data-testid="bounty-place-cancel" />,
}));

import ServiceRecordTab from '../ServiceRecordTab';

describe('ServiceRecordTab', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
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

  it('mounts personal-standing views, combat history, bounty board, and place/cancel', async () => {
    await act(async () => {
      root.render(<ServiceRecordTab />);
    });

    expect(container.querySelector('.sb-service-record')).toBeTruthy();
    expect(container.querySelector('[data-testid="rank-display"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="rank-progress"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="medal-showcase"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="combat-history-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bounty-board"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bounty-place-cancel"]')).toBeTruthy();
  });
});
