// @vitest-environment jsdom
/**
 * LEG-3600 — ConstructionVenue Space Engineer assignment panel.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConstructionVenue, { formatConstructionEngineerError } from '../ConstructionVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetOwnedPlanets = vi.fn();
const mockGetPlanetProfessions = vi.fn();

vi.mock('../../../services/api', () => ({
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
  gameAPI: {
    planetary: {
      getOwnedPlanets: (...args: unknown[]) => mockGetOwnedPlanets(...args),
    },
  },
  planetaryAPI: {
    getPlanetProfessions: (...args: unknown[]) => mockGetPlanetProfessions(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: { cargo: { contents: {} } },
    refreshPlayerState: vi.fn(),
    loadShips: vi.fn(),
  }),
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const RESERVATION = {
  id: 'res-eng-1',
  state: 'frame_assembly',
  ship_type: 'scout',
  ship_name: 'Engineer Scout',
  station_id: 'station-1',
  assigned_engineers: [],
  assigned_engineer_count: 0,
  queue_bonus_credit: 0,
  milestones: {},
  resources_required: { ore: 0, equipment: 0, organics: 0 },
  resources_delivered: { ore: 0, equipment: 0, organics: 0 },
};

const VENUE_PROPS = {
  stationId: 'station-1',
  stationName: 'Test Dock',
  tier: 'A' as const,
  credits: 100000,
  onCreditsDelta: vi.fn(),
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

describe('formatConstructionEngineerError Network Error densify (LEG-3600)', () => {
  const FALLBACK = 'Connection error. Please try again.';

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatConstructionEngineerError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatConstructionEngineerError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatConstructionEngineerError(new Error('Network Error'), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
  });
});

describe('ConstructionVenue — Space Engineer assignment panel (LEG-3600)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    mockGetOwnedPlanets.mockResolvedValue({
      planets: [{ id: 'planet-1', name: 'Forge Prime' }],
    });
    mockGetPlanetProfessions.mockResolvedValue({
      professions: { SPACE_ENGINEERS: 2 },
    });

    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/construction/quotes')) {
        return { ok: true, json: async () => ({ quotes: [] }) };
      }
      if (u.includes('/construction/reservations/mine')) {
        return { ok: true, json: async () => ({ reservations: [RESERVATION] }) };
      }
      if (u.includes('/assign-engineer') && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({
            assigned_engineer_count: 1,
            assigned_engineers: [{ planet_id: 'planet-1', count: 1 }],
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', fetchMock);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('assigns engineers from owned planet and never surfaces raw Network Error on failure', async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/construction/quotes')) {
        return { ok: true, json: async () => ({ quotes: [] }) };
      }
      if (u.includes('/construction/reservations/mine')) {
        return { ok: true, json: async () => ({ reservations: [RESERVATION] }) };
      }
      if (u.includes('/assign-engineer') && init?.method === 'POST') {
        return Promise.reject(new Error('Network Error'));
      }
      return { ok: true, json: async () => ({}) };
    });

    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const buildsTab = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('My Builds'),
    );
    expect(buildsTab).toBeTruthy();
    await act(async () => {
      buildsTab!.click();
      await flush();
    });

    const engineersBtn = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('Engineers'),
    );
    expect(engineersBtn).toBeTruthy();
    await act(async () => {
      engineersBtn!.click();
      await flush();
      await flush();
    });

    const assignBtn = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('Assign Engineers'),
    );
    expect(assignBtn).toBeTruthy();
    await act(async () => {
      assignBtn!.click();
      await flush();
      await flush();
    });

    expect(container.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).toMatch(/Connection error/i);
  });

  it('happy path posts assign-engineer with planet_id and count', async () => {
    await act(async () => {
      root.render(<ConstructionVenue {...VENUE_PROPS} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const buildsTab = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('My Builds'),
    );
    await act(async () => {
      buildsTab!.click();
      await flush();
    });

    const engineersBtn = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('Engineers'),
    );
    await act(async () => {
      engineersBtn!.click();
      await flush();
      await flush();
    });

    const assignBtn = Array.from(container.querySelectorAll('button')).find((btn) =>
      btn.textContent?.includes('Assign Engineers'),
    );
    await act(async () => {
      assignBtn!.click();
      await flush();
      await flush();
    });

    const assignCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).includes('/assign-engineer') && init?.method === 'POST',
    );
    expect(assignCall).toBeTruthy();
    const body = JSON.parse(String(assignCall![1]?.body));
    expect(body).toEqual({ planet_id: 'planet-1', count: 1 });
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
