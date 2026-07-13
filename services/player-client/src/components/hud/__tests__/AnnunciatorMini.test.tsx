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
      // WO-UI0-SHELL-TRANSPLANT NIT n1: HAZARD now needs hazard_level >= 5
      // (was > 0) -- see useAnnunciatorState.ts's HAZARD_ACTIVE_THRESHOLD.
      currentSector: { name: 'Sol', hazard_level: 5, radiation_level: 0 },
      markMessageRead,
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
    await flush();
  });

  /** Fresh unmount + remount -- the LAW poll only fires on the mount-time
   * effect (mirrors Annunciator.test.tsx's own remountFresh). */
  const remountFresh = async () => {
    act(() => {
      root.unmount();
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    render();
    await flush();
  };

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

  it('WO-UI0-SHELL-TRANSPLANT: bulbs/segments ALSO carry the bare artifact classes (color comes from cockpit-shell.css)', () => {
    // Companion classes, not a swap -- see AnnunciatorMini.tsx's own
    // doc-comment for why the mini variant keeps its size-override classes
    // alongside the bare ones instead of adopting them outright.
    expect(container.querySelectorAll('.bulb.annunciator-mini-bulb')).toHaveLength(2);
    expect(container.querySelectorAll('.seg.annunciator-mini-seg')).toHaveLength(5);
    expect(container.querySelector('.segs.annunciator-mini-segs')).not.toBeNull();
    // The mini variant never adopts `.annun` itself -- that's an absolute-
    // overlay contract, the opposite of this inline-row component.
    expect(container.querySelector('.annun')).toBeNull();
  });

  it('HAZARD (caution): live sector hazard (>= 5, NIT n1) lights the HAZ segment amber and the caution bulb', () => {
    const hazSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'HAZ')!;
    expect(hazSeg.classList.contains('livec')).toBe(true);
    expect(container.querySelectorAll('.annunciator-mini-bulb')[1].classList.contains('on')).toBe(true);
  });

  it('LAW: click requests the deck TACTICAL[THREAT] page (same click-through as the full strip)', () => {
    const lawSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'LAW')! as HTMLButtonElement;
    act(() => {
      lawSeg.click();
    });
    expect(requestTacticalPageSpy).toHaveBeenCalledWith('threat');
  });

  it('LAW (NIT n5): renders the WARN-red .live class, same as the full strip -- shared segLitClass()', async () => {
    mockGetGreyStatus.mockResolvedValue({ isGrey: true, kind: 'player_attack', greyUntil: null, remainingSeconds: 300, clearFineCredits: 500 });
    await remountFresh();

    const lawSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'LAW')!;
    expect(lawSeg.classList.contains('live')).toBe(true);
    expect(lawSeg.classList.contains('livec')).toBe(false);
    expect(container.querySelectorAll('.annunciator-mini-bulb')[1].classList.contains('on')).toBe(true); // still the CAUT bulb
  });

  it('every bulb/segment carries an aria-label', () => {
    container.querySelectorAll('.annunciator-mini-bulb, .annunciator-mini-seg').forEach((el) => {
      expect(el.getAttribute('aria-label')).toBeTruthy();
    });
  });
});
