// @vitest-environment jsdom
/**
 * useAnnunciatorState — the annunciator's shared trigger/lifecycle/nav
 * state machine (WO-HUD-LIGHTS phase 1), consumed identically by
 * Annunciator.tsx and AnnunciatorMini.tsx. No renderHook precedent exists
 * in this codebase yet (grepped) — this file establishes one: a tiny host
 * component calls the hook and stashes its return value into a
 * module-level mutable slot on every render (read back via `latest()`
 * after each `act()`), following the same createRoot+act idiom every
 * other test in this repo already uses, rather than pulling in
 * @testing-library/react's renderHook for a single hook.
 *
 * `useSectorContacts` (itself a hook composing useWebSocket+useGame) is
 * mocked entirely; `isLawArchetype`/`repBucket` — the pure classification
 * functions this hook actually exercises — are kept real via
 * vi.importActual so LAW/THREAT gating is tested against the genuine
 * logic, not a re-description of it.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SectorContact } from '../../tactical/contactClassification';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const {
  useGameMock,
  useMFDMock,
  selectPageMock,
  useSectorContactsMock,
  requestTacticalPageMock,
  getOwnedPlanetsMock,
} = vi.hoisted(() => ({
  useGameMock: vi.fn(),
  useMFDMock: vi.fn(),
  selectPageMock: vi.fn(),
  useSectorContactsMock: vi.fn(),
  requestTacticalPageMock: vi.fn(),
  getOwnedPlanetsMock: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: useGameMock,
}));

vi.mock('../../mfd/MFDContext', () => ({
  useMFD: useMFDMock,
}));

vi.mock('../../../services/api', () => ({
  planetaryAPI: { getOwnedPlanets: getOwnedPlanetsMock },
}));

vi.mock('../../../services/deckNavBus', () => ({
  requestTacticalPage: requestTacticalPageMock,
}));

vi.mock('../../tactical/contactClassification', async () => {
  const actual = await vi.importActual<typeof import('../../tactical/contactClassification')>(
    '../../tactical/contactClassification'
  );
  return { ...actual, useSectorContacts: useSectorContactsMock };
});

import { useAnnunciatorState, segLitClass, roleFor, ariaLiveFor, type AnnunciatorState } from '../useAnnunciatorState';

let latestState: AnnunciatorState | null = null;

function Harness() {
  latestState = useAnnunciatorState();
  return null;
}

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const sector = (hazard: number) => ({
  id: 1,
  sector_id: 1,
  name: 'Test Sector',
  type: 'normal',
  hazard_level: hazard,
  radiation_level: 0,
  resources: {},
  players_present: [],
});

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

const mount = async () => {
  await act(async () => {
    root.render(<Harness />);
  });
};

beforeEach(() => {
  latestState = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);

  useGameMock.mockReturnValue({
    playerState: { id: 'p1', bounty_total: 0 },
    currentSector: sector(0),
    unreadMessageCount: 0,
  });
  useMFDMock.mockReturnValue({ selectPage: selectPageMock });
  useSectorContactsMock.mockReturnValue([]);
  getOwnedPlanetsMock.mockResolvedValue({ planets: [] });

  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });

  vi.clearAllMocks();
  // clearAllMocks above wipes the default return values set up right before
  // it too (they're jest/vitest mock impls) -- re-seed the ones every test
  // needs a baseline for.
  useGameMock.mockReturnValue({
    playerState: { id: 'p1', bounty_total: 0 },
    currentSector: sector(0),
    unreadMessageCount: 0,
  });
  useMFDMock.mockReturnValue({ selectPage: selectPageMock });
  useSectorContactsMock.mockReturnValue([]);
  getOwnedPlanetsMock.mockResolvedValue({ planets: [] });
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

describe('segLitClass / roleFor / ariaLiveFor — pure mappings', () => {
  it('maps each severity to its cockpit-shell.css lit class', () => {
    expect(segLitClass({ severity: 'warn' } as any)).toBe('live');
    expect(segLitClass({ severity: 'caution' } as any)).toBe('livec');
    expect(segLitClass({ severity: 'info' } as any)).toBe('livecm');
  });

  it('maps warn/ALERT to the assertive alert role, everything else to polite status', () => {
    expect(roleFor('warn')).toBe('alert');
    expect(roleFor('ALERT')).toBe('alert');
    expect(roleFor('caution')).toBe('status');
    expect(roleFor('info')).toBe('status');
    expect(ariaLiveFor('warn')).toBe('assertive');
    expect(ariaLiveFor('ALERT')).toBe('assertive');
    expect(ariaLiveFor('caution')).toBe('polite');
    expect(ariaLiveFor('info')).toBe('polite');
  });
});

describe('useAnnunciatorState — HAZARD segment', () => {
  it('is inactive below the threshold and active at/above it', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(4), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.segments.find((s) => s.id === 'HAZARD')!.active).toBe(false);

    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(5), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.segments.find((s) => s.id === 'HAZARD')!.active).toBe(true);
    expect(latestState!.segments.find((s) => s.id === 'HAZARD')!.ariaLabel).toContain('hazard level 5 of 10');
  });

  it('opens and closes the hazard card via onActivate/closeHazardCard', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(9), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.hazardCardOpen).toBe(false);

    await act(async () => {
      latestState!.segments.find((s) => s.id === 'HAZARD')!.onActivate();
    });
    expect(latestState!.hazardCardOpen).toBe(true);

    await act(async () => {
      latestState!.closeHazardCard();
    });
    expect(latestState!.hazardCardOpen).toBe(false);
  });
});

describe('useAnnunciatorState — LAW / THREAT segments (real classification logic)', () => {
  const lawContact: SectorContact = { id: 'npc-law', is_npc: true, archetype: 'STATION_SECURITY' };
  const redContact: SectorContact = { id: 'npc-red', is_npc: true, archetype: 'HOSTILE_RAIDER' };
  const cleanContact: SectorContact = { id: 'npc-clean', is_npc: true, archetype: 'FREIGHT_HAULER', notoriety: 0 };

  it('LAW lights for a law-archetype contact and navigates to TACTICAL THREAT page on activate', async () => {
    useSectorContactsMock.mockReturnValue([lawContact]);
    await mount();
    const law = latestState!.segments.find((s) => s.id === 'LAW')!;
    expect(law.active).toBe(true);

    await act(async () => {
      law.onActivate();
    });
    expect(requestTacticalPageMock).toHaveBeenCalledWith('threat');
  });

  it('THREAT lights for a red/gray-bucket contact and navigates to TACTICAL TARGET page on activate', async () => {
    useSectorContactsMock.mockReturnValue([redContact]);
    await mount();
    const threat = latestState!.segments.find((s) => s.id === 'THREAT')!;
    expect(threat.active).toBe(true);

    await act(async () => {
      threat.onActivate();
    });
    expect(requestTacticalPageMock).toHaveBeenCalledWith('target');
  });

  it('neither LAW nor THREAT light for a clean, non-law contact', async () => {
    useSectorContactsMock.mockReturnValue([cleanContact]);
    await mount();
    expect(latestState!.segments.find((s) => s.id === 'LAW')!.active).toBe(false);
    expect(latestState!.segments.find((s) => s.id === 'THREAT')!.active).toBe(false);
  });
});

describe('useAnnunciatorState — COMM segment', () => {
  it('is inactive at zero unread and active with a count, pluralizing the aria label', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(0), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.segments.find((s) => s.id === 'COMM')!.active).toBe(false);

    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(0), unreadMessageCount: 1 });
    await mount();
    let comm = latestState!.segments.find((s) => s.id === 'COMM')!;
    expect(comm.active).toBe(true);
    expect(comm.ariaLabel).toContain('1 unread message —');

    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(0), unreadMessageCount: 3 });
    await mount();
    comm = latestState!.segments.find((s) => s.id === 'COMM')!;
    expect(comm.ariaLabel).toContain('3 unread messages —');
  });

  it('selects the comms-crew page at both possible screenIds on activate', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(0), unreadMessageCount: 1 });
    await mount();
    await act(async () => {
      latestState!.segments.find((s) => s.id === 'COMM')!.onActivate();
    });
    expect(selectPageMock).toHaveBeenCalledWith('sidebar-b', 'comms-crew');
    expect(selectPageMock).toHaveBeenCalledWith('sidebar-a-folded', 'comms-crew');
  });
});

describe('useAnnunciatorState — ALERT master consolidation + lamp lifecycle', () => {
  it('lights with a segment active, and ack silences the flash without clearing active', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(9), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.alert.active).toBe(true);
    expect(latestState!.alert.flashing).toBe(true);

    await act(async () => {
      latestState!.alert.ack();
    });
    expect(latestState!.alert.active).toBe(true);
    expect(latestState!.alert.flashing).toBe(false);
    expect(latestState!.alert.ariaLabel).toBe('Master alert — active, acknowledged');
  });

  it('re-arms the flash on a fresh false-to-true edge even after a prior ack', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(9), unreadMessageCount: 0 });
    await mount();
    await act(async () => {
      latestState!.alert.ack();
    });
    expect(latestState!.alert.flashing).toBe(false);

    // Drop the trigger to false, then bring it back -- a fresh edge.
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(0), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.alert.active).toBe(false);

    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(9), unreadMessageCount: 0 });
    await mount();
    expect(latestState!.alert.active).toBe(true);
    expect(latestState!.alert.flashing).toBe(true);
  });

  it('reports idle as "clear" in the aria label', async () => {
    await mount();
    expect(latestState!.alert.active).toBe(false);
    expect(latestState!.alert.ariaLabel).toBe('Master alert — clear');
  });

  it('lights the master with ZERO segments lit for a bounty-only condition (intended asymmetry)', async () => {
    useGameMock.mockReturnValue({
      playerState: { id: 'p1', bounty_total: 500 },
      currentSector: sector(0),
      unreadMessageCount: 0,
    });
    await mount();
    expect(latestState!.alert.active).toBe(true);
    expect(latestState!.segments.every((s) => !s.active)).toBe(true);
  });

  it('lights the master with ZERO segments lit for a siege-only condition (intended asymmetry)', async () => {
    getOwnedPlanetsMock.mockResolvedValue({ planets: [{ underSiege: true }] });
    useGameMock.mockReturnValue({
      playerState: { id: 'p1', bounty_total: 0 },
      currentSector: sector(0),
      unreadMessageCount: 0,
    });
    await mount();
    await flush();
    expect(getOwnedPlanetsMock).toHaveBeenCalled();
    expect(latestState!.alert.active).toBe(true);
    expect(latestState!.segments.every((s) => !s.active)).toBe(true);
  });

  it('never polls siege when there is no playerState', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: sector(0), unreadMessageCount: 0 });
    await mount();
    await flush();
    expect(getOwnedPlanetsMock).not.toHaveBeenCalled();
  });
});

describe('useAnnunciatorState — passthrough state', () => {
  it('passes currentSector through, defaulting to null', async () => {
    useGameMock.mockReturnValue({ playerState: null, currentSector: null, unreadMessageCount: 0 });
    await mount();
    expect(latestState!.currentSector).toBeNull();
  });

  it('exposes reducedMotion from matchMedia', async () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    });
    await mount();
    expect(latestState!.reducedMotion).toBe(true);
  });
});
