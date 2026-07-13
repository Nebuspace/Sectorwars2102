// @vitest-environment jsdom
/**
 * AnnunciatorMini (WO-UI1-CHROME-COMPLETE item 7) — the compact monitor-
 * header variant. Reads the SAME useAnnunciatorState() hook as the full
 * strip (already exhaustively covered by Annunciator.test.tsx — every
 * trigger/lifecycle/navigation branch), so this file is a proportionate
 * smoke test: mount shape, abbreviated labels, one representative trigger
 * per severity class, and a11y — not a full re-run of every branch.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const markMessageRead = vi.fn().mockResolvedValue(undefined);
const mockGetGreyStatus = vi.fn();
const mockGetOwnedPlanets = vi.fn();

let mockGameState: {
  playerState: { id: string; turns: number; bounty_total?: number } | null;
  currentSector: { name?: string; hazard_level: number; radiation_level?: number } | null;
  markMessageRead: typeof markMessageRead;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    npcCombatSignal: 0,
    lastNpcCombatInitiated: null,
    newMessageSignal: 0,
    lastNewMessage: null,
  }),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

vi.mock('../../../services/api', () => ({
  greyStatusAPI: { getStatus: (...a: unknown[]) => mockGetGreyStatus(...a) },
  planetaryAPI: { getOwnedPlanets: (...a: unknown[]) => mockGetOwnedPlanets(...a) },
}));

import { MFDProvider } from '../../mfd/MFDContext';
import * as deckNavBus from '../../../services/deckNavBus';
import AnnunciatorMini from '../AnnunciatorMini';

describe('AnnunciatorMini', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let requestTacticalPageSpy: ReturnType<typeof vi.spyOn>;

  const render = () => {
    act(() => {
      root.render(
        <MFDProvider>
          <AnnunciatorMini />
        </MFDProvider>
      );
    });
  };

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  beforeEach(async () => {
    markMessageRead.mockClear();
    mockGetGreyStatus.mockReset().mockResolvedValue({ isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null });
    mockGetOwnedPlanets.mockReset().mockResolvedValue({ planets: [] });
    requestTacticalPageSpy = vi.spyOn(deckNavBus, 'requestTacticalPage');
    mockGameState = {
      playerState: { id: 'player-1', turns: 500, bounty_total: 0 },
      currentSector: { name: 'Sol', hazard_level: 3, radiation_level: 0 },
      markMessageRead,
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
    await flush();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    requestTacticalPageSpy.mockRestore();
  });

  it('renders the compact strip: 2 abbreviated bulbs + 5 abbreviated segments', () => {
    expect(container.querySelector('[data-testid="annunciator-mini"]')).not.toBeNull();
    const bulbs = container.querySelectorAll('.annunciator-mini-bulb');
    expect(bulbs).toHaveLength(2);
    expect(bulbs[0].textContent).toBe('W');
    expect(bulbs[1].textContent).toBe('C');

    const segs = Array.from(container.querySelectorAll('.annunciator-mini-seg')).map((el) => el.textContent);
    expect(segs).toEqual(['HAZ', 'LAW', 'THR', 'TRN', 'COM']);
  });

  it('HAZARD (caution): live sector hazard lights the HAZ segment and the caution bulb', () => {
    const hazSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'HAZ')!;
    expect(hazSeg.classList.contains('is-live')).toBe(true);
    expect(container.querySelectorAll('.annunciator-mini-bulb')[1].classList.contains('on')).toBe(true);
  });

  it('LAW: click requests the deck TACTICAL[THREAT] page (same click-through as the full strip)', () => {
    const lawSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'LAW')! as HTMLButtonElement;
    act(() => {
      lawSeg.click();
    });
    expect(requestTacticalPageSpy).toHaveBeenCalledWith('threat');
  });

  it('every bulb/segment carries an aria-label', () => {
    container.querySelectorAll('.annunciator-mini-bulb, .annunciator-mini-seg').forEach((el) => {
      expect(el.getAttribute('aria-label')).toBeTruthy();
    });
  });
});
