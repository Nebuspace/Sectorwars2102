// @vitest-environment jsdom
/**
 * AnnunciatorMini (WO-HUD-LIGHTS phase 1) — the compact monitor-header
 * variant. Reads the SAME useAnnunciatorState() hook as the full strip
 * (already exhaustively covered by Annunciator.test.tsx — every
 * trigger/lifecycle/navigation branch), so this file stays a proportionate
 * smoke test: mount shape, abbreviated labels, one representative trigger,
 * and a11y — not a full re-run of every branch. Still unmounted anywhere in
 * the app (see AnnunciatorMini.tsx's own doc-comment) — updated here only
 * because it shares the hook's now-changed return shape.
 *
 * services/api is mocked (planetaryAPI.getOwnedPlanets, the siege poll
 * restored by hub ruling) so this passive smoke suite never fires a real,
 * unmocked network call — mirrors Annunciator.test.tsx's own mock.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface MockContact {
  user_id?: string;
  player_id?: string;
  username?: string;
  is_npc?: boolean;
  archetype?: string;
  notoriety?: number;
  reputation_tier?: string;
}

let mockGameState: {
  playerState: { id: string; username?: string } | null;
  currentSector: { name?: string; hazard_level: number; radiation_level?: number; players_present?: MockContact[] } | null;
  unreadMessageCount: number;
};

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sectorPlayers: [] }),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => mockGameState,
}));

vi.mock('../../../services/api', () => ({
  planetaryAPI: { getOwnedPlanets: () => Promise.resolve({ planets: [] }) },
}));

import { MFDProvider } from '../../mfd/MFDContext';
import * as deckNavBus from '../../../services/deckNavBus';
import AnnunciatorMini from '../AnnunciatorMini';

const LAW_CONTACT: MockContact = { player_id: 'npc-law-1', is_npc: true, archetype: 'LAW_ENFORCEMENT', username: 'Marshal Vex' };

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
    requestTacticalPageSpy = vi.spyOn(deckNavBus, 'requestTacticalPage');
    mockGameState = {
      playerState: { id: 'player-1', username: 'commander' },
      // hazard_level starts at 5 (>= HAZARD_ACTIVE_THRESHOLD) so the one
      // representative "lit" smoke test below doesn't need a rerender.
      currentSector: { name: 'Sol', hazard_level: 5, radiation_level: 0, players_present: [] },
      unreadMessageCount: 0,
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

  it('renders the compact strip: 1 abbreviated master bulb + 4 abbreviated segments', () => {
    expect(container.querySelector('[data-testid="annunciator-mini"]')).not.toBeNull();
    const bulbs = container.querySelectorAll('.annunciator-mini-bulb');
    expect(bulbs).toHaveLength(1);
    expect(bulbs[0].textContent).toBe('A');

    const segs = Array.from(container.querySelectorAll('.annunciator-mini-seg')).map((el) => el.textContent);
    expect(segs).toEqual(['HAZ', 'LAW', 'THR', 'COM']);
  });

  it('bulb/segments carry the bare artifact classes (color comes from cockpit-shell.css)', () => {
    expect(container.querySelectorAll('.bulb.annunciator-mini-bulb')).toHaveLength(1);
    expect(container.querySelectorAll('.seg.annunciator-mini-seg')).toHaveLength(4);
    expect(container.querySelector('.segs.annunciator-mini-segs')).not.toBeNull();
    // The mini variant never adopts `.annun` itself -- that's an absolute-
    // overlay contract, the opposite of this inline-row component.
    expect(container.querySelector('.annun')).toBeNull();
  });

  it('HAZARD (caution): live sector hazard (>= 5) lights the HAZ segment amber and the master bulb', () => {
    const hazSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'HAZ')!;
    expect(hazSeg.classList.contains('livec')).toBe(true);
    expect(container.querySelector('.annunciator-mini-bulb')!.classList.contains('on')).toBe(true);
  });

  it('LAW: lights on a natural caution `.livec` class (no red special-case anymore) and click requests TACTICAL[THREAT]', async () => {
    mockGameState = { ...mockGameState, currentSector: { ...mockGameState.currentSector!, players_present: [LAW_CONTACT] } };
    render();
    await flush();

    const lawSeg = Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === 'LAW')! as HTMLButtonElement;
    expect(lawSeg.classList.contains('livec')).toBe(true);
    expect(lawSeg.classList.contains('live')).toBe(false);

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

  it('Pixel REVISE: role/aria-live are ALWAYS present (idle included), not toggled on activation -- matches the mounted strip', async () => {
    // Fully idle, unlike this suite's default beforeEach (hazard_level: 5).
    mockGameState = {
      playerState: { id: 'player-1', username: 'commander' },
      currentSector: { name: 'Sol', hazard_level: 0, radiation_level: 0, players_present: [] },
      unreadMessageCount: 0,
    };
    render();
    await flush();

    const bulb = container.querySelector('.annunciator-mini-bulb')!;
    expect(bulb.classList.contains('on')).toBe(false); // confirms this is really the idle case
    expect(bulb.getAttribute('role')).toBe('alert');
    expect(bulb.getAttribute('aria-live')).toBe('assertive');

    const segByLabel = (label: string) =>
      Array.from(container.querySelectorAll('.annunciator-mini-seg')).find((el) => el.textContent === label)!;
    expect(segByLabel('THR').getAttribute('role')).toBe('alert'); // warn-severity
    expect(segByLabel('THR').getAttribute('aria-live')).toBe('assertive');
    expect(segByLabel('HAZ').getAttribute('role')).toBe('status'); // caution-severity
    expect(segByLabel('LAW').getAttribute('role')).toBe('status');
    expect(segByLabel('COM').getAttribute('role')).toBe('status'); // info-severity
    expect(segByLabel('COM').getAttribute('aria-live')).toBe('polite');
  });
});
